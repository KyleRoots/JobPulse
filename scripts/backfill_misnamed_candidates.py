#!/usr/bin/env python3
"""
Backfill: detect Bullhorn candidates whose first/last name matches the
work-authorization / citizenship blocklist and propose corrections.

Background
----------
Until April 2026 a regex heuristic in resume_parser.py could pick up a
"Canadian Citizen" / "US Citizen" / "Permanent Resident" header line
from a resume and ship it to Bullhorn as the candidate's first/last
name. The heuristic has been hardened, but historical records remain.

Behaviour
---------
By default this script runs in DRY-RUN mode:

    python scripts/backfill_misnamed_candidates.py

It searches Bullhorn for candidates whose firstName or lastName matches
any token in ``utils.candidate_name_extraction.WORK_AUTH_TOKENS`` (or
matches a known work-auth phrase from ``WORK_AUTH_PHRASES``), prints
each match with its current name, and writes a CSV audit file
(``misnamed_candidates_<timestamp>.csv``) recording the candidate id,
current name, and proposed replacement. No edits are made.

After reviewing the CSV, run the same script with ``--apply`` to apply
the corrections. For every candidate, the script will:

  1. Pull the candidate's most recent resume from Bullhorn.
  2. Re-parse the resume with the (now hardened) ResumeParser.
  3. If the parser yields a valid name (passes is_valid_name), PATCH
     the candidate record via bullhorn_service.update_candidate(...).
     The return value is checked; ``None`` is treated as a failure
     and recorded in the audit CSV with the HTTP context.
  4. Append the result to the audit CSV.

Both modes are safe to re-run; the script never deletes data and only
PATCHes when a confidently-better name is available.

Usage
-----
    # Dry run (default) — no writes, just produces audit CSV.
    python scripts/backfill_misnamed_candidates.py

    # Restrict to a single Bullhorn candidate id (useful for the
    # known production case).
    python scripts/backfill_misnamed_candidates.py --candidate-id 4648428

    # Apply corrections after reviewing the dry-run output.
    python scripts/backfill_misnamed_candidates.py --apply
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Ensure project root on sys.path so this script works when invoked
# directly from the scripts/ directory.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app import app, get_bullhorn_service  # noqa: E402
from utils.candidate_name_extraction import (  # noqa: E402
    WORK_AUTH_PHRASES,
    WORK_AUTH_TOKENS,
    is_valid_name,
    is_work_auth_phrase,
)

# Common short words inside multi-word phrases that are useless as
# Bullhorn search terms (every candidate would match) — exclude them
# when deriving search terms from WORK_AUTH_PHRASES.
_PHRASE_SEARCH_STOPWORDS = {
    "to", "the", "of", "for", "a", "an", "at", "in", "on", "with",
}

# Words that often appear at the very top of a resume (or in cover-
# letter / email-body fragments stored as PDFs) and that
# ``ResumeParser._parse_text`` will sometimes mistake for a candidate
# name. ``is_valid_name`` accepts them because they're capitalized
# alphabetic tokens — so we add this script-local guard to PREVENT the
# backfill from "fixing" a misnamed candidate by replacing it with
# another piece of garbage.
#
# Tokens are stored uppercase. The guard rejects the proposal if EITHER
# the first or last name (uppercased) appears in this set.
_SECTION_HEADING_BLOCKLIST = {
    # Resume section headings
    "PROFESSIONAL", "SUMMARY", "OBJECTIVE", "EXPERIENCE", "EDUCATION",
    "SKILLS", "QUALIFICATIONS", "PROFILE", "CONTACT", "RESUME",
    "CURRICULUM", "VITAE", "CV", "TECHNICAL", "CAREER", "EMPLOYMENT",
    "REFERENCES", "ACHIEVEMENTS", "ACCOMPLISHMENTS", "CERTIFICATIONS",
    "AWARDS", "LANGUAGES", "INTERESTS", "HOBBIES", "PROJECTS",
    "ABOUT", "OVERVIEW", "HIGHLIGHTS", "EXPERTISE", "COMPETENCIES",
    # Cover-letter / email-body openers and closers
    "GOOD", "WISHES", "GREETINGS", "REGARDS", "HELLO", "DEAR",
    "THANKS", "THANK", "BEST", "KIND", "SINCERELY", "COVER",
    "LETTER", "ATTACHED", "ATTACHMENT", "SUBJECT", "FROM", "SENT",
}


def _is_section_heading_name(first: Optional[str], last: Optional[str]) -> bool:
    """Return True if the proposed name looks like a resume section
    heading or cover-letter opener rather than an actual person's
    name. Used as a script-local safety net on top of ``is_valid_name``
    to prevent false-positive auto-corrections in the backfill flow.
    """
    for value in (first, last):
        if not value:
            continue
        # Each value can be one or more whitespace-separated tokens
        # (e.g. last_name = "Dzifa Denoo"). Reject if ANY token is in
        # the blocklist.
        for tok in str(value).split():
            if tok.strip(".,;:").upper() in _SECTION_HEADING_BLOCKLIST:
                return True
    return False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("backfill_misnamed_candidates")


def _ensure_authenticated(bh_service) -> bool:
    """Make sure the Bullhorn client has a live session token."""
    if bh_service.base_url and bh_service.rest_token:
        return True
    return bool(bh_service.authenticate())


def _phrase_derived_search_terms() -> set:
    """Return single-word terms extracted from WORK_AUTH_PHRASES that
    are useful as Bullhorn name-field search terms.

    These are words like ``green``, ``permanent``, ``authorized`` —
    intentionally NOT in WORK_AUTH_TOKENS because they're ambiguous
    (real surnames exist). For backfill discovery though, we still
    need to surface candidates whose name happens to be the work-auth
    form ("Green Card", "Permanent Resident") — Bullhorn's search
    matches on individual indexed words, so we query for these tokens
    too and rely on the local ``is_work_auth_phrase`` filter to weed
    out the real-surname false positives after the fact.
    """
    derived: set = set()
    for phrase in WORK_AUTH_PHRASES:
        for word in phrase.split():
            if word in _PHRASE_SEARCH_STOPWORDS:
                continue
            if len(word) <= 2:
                continue
            derived.add(word)
    return derived


def _build_search_query() -> str:
    """Build a Bullhorn Lucene query that finds any candidate whose
    firstName OR lastName equals one of the work-auth tokens — or one
    of the words derived from a known work-auth phrase.

    Two sources are merged so the backfill catches both bug shapes:

      * Single-token misnames like ``"Akhil Citizen"`` (from
        WORK_AUTH_TOKENS).
      * Phrase-only misnames like ``"Green Card"`` or
        ``"Permanent Resident"`` (from phrase-derived terms).

    The combined term set is deduplicated. Each surviving term becomes
    two Lucene clauses, joined with OR. After the search returns,
    ``is_work_auth_phrase`` is applied locally to discard real-surname
    false positives like "John Green".
    """
    seen_terms: set = set()
    for tok in WORK_AUTH_TOKENS:
        # Skip very short tokens — too noisy as exact-name match
        # candidates and unlikely to have hit production.
        if len(tok) <= 2:
            continue
        seen_terms.add(tok)
    seen_terms.update(_phrase_derived_search_terms())

    field_clauses = []
    for tok in sorted(seen_terms):
        field_clauses.append(f'firstName:"{tok}"')
        field_clauses.append(f'lastName:"{tok}"')
    return " OR ".join(field_clauses)


def _find_misnamed_candidates(bh_service, candidate_id: Optional[int]) -> List[Dict]:
    """Return a list of candidate dicts whose names match the blocklist.

    Uses the Bullhorn REST search/Candidate endpoint directly via the
    authenticated session on ``bh_service`` (the same pattern the
    BullhornService class uses internally — base_url + BhRestToken
    query param).
    """
    if candidate_id:
        cand = bh_service.get_candidate(candidate_id)
        if not cand:
            log.warning("Candidate id %s not found in Bullhorn.", candidate_id)
            return []
        return [cand]

    if not _ensure_authenticated(bh_service):
        log.error("Could not authenticate to Bullhorn — aborting.")
        return []

    query = _build_search_query()
    log.info("Searching Bullhorn with query: %s", query)

    matches: List[Dict] = []
    start = 0
    page_size = 200
    fields = "id,firstName,lastName,email,phone,dateAdded,source"
    url = f"{bh_service.base_url}search/Candidate"

    while True:
        params = {
            "query": query,
            "fields": fields,
            "count": page_size,
            "start": start,
            "sort": "id",
            "BhRestToken": bh_service.rest_token,
        }
        try:
            resp = bh_service.session.get(url, params=params, timeout=30)
        except Exception as exc:
            log.error("Search request failed at start=%d: %s", start, exc)
            break

        # On 401, force a re-auth and retry the page once.
        if resp.status_code == 401:
            log.info("Token expired during search — re-authenticating.")
            bh_service.rest_token = None
            if not _ensure_authenticated(bh_service):
                log.error("Re-authentication failed — aborting search.")
                break
            params["BhRestToken"] = bh_service.rest_token
            resp = bh_service.session.get(url, params=params, timeout=30)

        if resp.status_code != 200:
            log.error("Search returned %s at start=%d: %s",
                      resp.status_code, start, resp.text[:200])
            break

        try:
            payload = resp.json()
        except Exception as exc:
            log.error("Could not parse search response JSON: %s", exc)
            break

        page = payload.get("data", []) or []
        if not page:
            break

        for cand in page:
            first = (cand.get("firstName") or "").strip()
            last = (cand.get("lastName") or "").strip()
            # Defence in depth — apply our own validator. This filters
            # out any false positives where Bullhorn's index matched
            # an unrelated token in a real name.
            if not is_valid_name(first, last):
                if is_work_auth_phrase(f"{first} {last}"):
                    matches.append(cand)

        if len(page) < page_size:
            break
        start += page_size

    log.info("Found %d candidate(s) matching the blocklist.", len(matches))
    return matches


def _pull_latest_resume_text(bh_service, candidate_id: int) -> Tuple[Optional[Tuple[str, Dict]], Optional[str]]:
    """Fetch the most recent resume file from a Bullhorn candidate and
    return ((raw_text, parsed_data), filename). Returns (None, None) on
    failure with a structured per-step skip-reason logged.

    Mirrors the proven pattern in screening/detection.py:
      * Bullhorn returns the file list under the ``EntityFiles`` key
        (capital E). Some tenants/versions also use ``entityFiles`` —
        we check both. Earlier versions of this script used only the
        lowercase form, which silently produced an empty list and
        skipped every candidate.
      * The file-download endpoint may return raw bytes OR a JSON
        envelope ``{"File": {"fileContent": "<base64>"}}`` — sniff the
        first byte to decide.
    """
    if not _ensure_authenticated(bh_service):
        log.info("  -> SKIP REASON: bullhorn auth failed for candidate %s",
                 candidate_id)
        return None, None

    base = bh_service.base_url
    token = bh_service.rest_token

    # List files attached to the candidate.
    try:
        resp = bh_service.session.get(
            f"{base}entityFiles/Candidate/{candidate_id}",
            params={"BhRestToken": token},
            timeout=30,
        )
    except Exception as exc:
        log.warning("  -> SKIP REASON: list files failed for candidate %s: %s",
                    candidate_id, exc)
        return None, None

    if resp.status_code != 200:
        log.warning("  -> SKIP REASON: list files returned HTTP %s for candidate %s",
                    resp.status_code, candidate_id)
        return None, None

    payload = resp.json() or {}
    files = payload.get("EntityFiles") or payload.get("entityFiles") or []
    if not files:
        log.info("  -> SKIP REASON: no files attached in Bullhorn for candidate %s",
                 candidate_id)
        return None, None

    # File priority — prefer files explicitly tagged or named "resume",
    # then fall back to the newest .pdf/.docx/.doc, then to the newest
    # file regardless of extension (Bullhorn doesn't always include an
    # extension on the name field).
    files.sort(key=lambda f: f.get("dateAdded") or 0, reverse=True)
    chosen = None
    for f in files:
        ftype = (f.get("type") or "").lower()
        fname = (f.get("name") or "").lower()
        if "resume" in ftype or "resume" in fname:
            chosen = f
            break
    if not chosen:
        for f in files:
            fname = (f.get("name") or "").lower()
            if any(k in fname for k in (".pdf", ".docx", ".doc")):
                chosen = f
                break
    if not chosen:
        chosen = files[0]

    chosen_name = chosen.get("name") or f"resume_{candidate_id}"
    file_id = chosen.get("id")

    try:
        file_resp = bh_service.session.get(
            f"{base}file/Candidate/{candidate_id}/{file_id}",
            params={"BhRestToken": token},
            timeout=60,
        )
    except Exception as exc:
        log.warning("  -> SKIP REASON: file download failed for %s/%s: %s",
                    candidate_id, file_id, exc)
        return None, chosen_name

    if file_resp.status_code != 200:
        log.warning("  -> SKIP REASON: file download returned HTTP %s for %s/%s",
                    file_resp.status_code, candidate_id, file_id)
        return None, chosen_name

    # Bullhorn returns either raw file bytes OR a JSON envelope.
    raw_content = file_resp.content or b""
    raw_bytes: bytes
    if raw_content.lstrip()[:1] == b"{" and b'"File"' in raw_content[:200]:
        try:
            import base64 as _b64
            import json as _json
            envelope = _json.loads(raw_content)
            file_obj = envelope.get("File") or {}
            b64_content = file_obj.get("fileContent") or ""
            if not b64_content:
                log.warning("  -> SKIP REASON: JSON envelope has empty fileContent for %s/%s",
                            candidate_id, file_id)
                return None, chosen_name
            raw_bytes = _b64.b64decode(b64_content)
        except Exception as exc:
            log.warning("  -> SKIP REASON: failed to unwrap JSON envelope for %s/%s: %s",
                        candidate_id, file_id, exc)
            return None, chosen_name
    else:
        raw_bytes = raw_content

    if not raw_bytes:
        log.warning("  -> SKIP REASON: empty file body for %s/%s",
                    candidate_id, file_id)
        return None, chosen_name

    import os
    import tempfile

    # ResumeParser.parse_resume(file) accepts Union[FileStorage, str].
    # Passing a BytesIO does NOT work — the parser reads ``file.filename``
    # (the FileStorage attribute), which BytesIO does not have, and the
    # AttributeError is swallowed into an empty raw_text. So write the
    # bytes to a real temp file with the correct extension and pass the
    # path. This also enables the AI Vision OCR fallback (default-on
    # when quick_mode=False) for image-based / scanned PDF resumes.
    suffix = ""
    for ext in (".pdf", ".docx", ".doc"):
        if chosen_name.lower().endswith(ext):
            suffix = ext
            break
    if not suffix:
        # Try to sniff from magic bytes — Bullhorn sometimes omits the
        # extension on the name field even though the file IS a PDF.
        head = raw_bytes[:4]
        if head.startswith(b"%PDF"):
            suffix = ".pdf"
        elif head.startswith(b"PK"):
            suffix = ".docx"
        else:
            suffix = ".pdf"

    log.info("  -> Downloaded %d bytes for %s (%s); parsing...",
             len(raw_bytes), chosen_name, suffix)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name

        from resume_parser import ResumeParser
        parser = ResumeParser()
        try:
            parsed = parser.parse_resume(tmp_path)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("  -> SKIP REASON: parse_resume raised for %s: %s",
                        candidate_id, exc)
            return None, chosen_name

        if not parsed or not parsed.get("success"):
            err = (parsed or {}).get("error") if parsed else "no result"
            log.warning("  -> SKIP REASON: parse_resume success=False for %s: %s",
                        candidate_id, err)
            return None, chosen_name

        raw_text = parsed.get("raw_text") or ""
        parsed_data = parsed.get("parsed_data") or {}
        log.info("  -> Parsed %d chars; pipeline name = %r %r",
                 len(raw_text),
                 parsed_data.get("first_name"),
                 parsed_data.get("last_name"))
        return (raw_text, parsed_data), chosen_name
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _propose_correction(bh_service, candidate: Dict) -> Optional[Tuple[str, str]]:
    """Return (proposed_first, proposed_last) by re-parsing the resume.

    Prefers the full-pipeline result from ``ResumeParser.parse_resume``
    (which includes AI Vision OCR for scanned PDFs). Falls back to a
    direct ``_parse_text`` call on the raw text if the full pipeline
    didn't yield a valid name.
    """
    cand_id = candidate.get("id")
    payload, _filename = _pull_latest_resume_text(bh_service, cand_id)
    if not payload:
        return None

    raw_text, parsed_data = payload

    # First choice: the full-pipeline parsed_data (already validated
    # against the hardened heuristic + OCR fallback).
    pipe_first = (parsed_data or {}).get("first_name")
    pipe_last = (parsed_data or {}).get("last_name")
    if is_valid_name(pipe_first, pipe_last):
        if _is_section_heading_name(pipe_first, pipe_last):
            log.warning(
                "  -> SKIP REASON: pipeline name %r %r looks like a resume "
                "section heading or cover-letter opener — refusing to "
                "auto-replace with another piece of garbage",
                pipe_first, pipe_last,
            )
        else:
            return pipe_first, pipe_last
    elif pipe_first or pipe_last:
        log.info("  -> Pipeline name %r %r failed is_valid_name; trying heuristic fallback",
                 pipe_first, pipe_last)

    # Fallback: re-run _parse_text on raw_text in case the cached
    # parsed_data is stale (e.g. cached pre-fix result). This will use
    # the freshly-loaded hardened heuristic.
    if raw_text:
        from resume_parser import ResumeParser
        parser = ResumeParser()
        parsed = parser._parse_text(raw_text)
        new_first = parsed.get("first_name")
        new_last = parsed.get("last_name")
        if is_valid_name(new_first, new_last):
            if _is_section_heading_name(new_first, new_last):
                log.warning(
                    "  -> SKIP REASON: fallback name %r %r looks like a "
                    "resume section heading — refusing to auto-replace",
                    new_first, new_last,
                )
            else:
                return new_first, new_last

    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually PATCH Bullhorn records. Default is dry-run.")
    ap.add_argument("--candidate-id", type=int, default=None,
                    help="Limit to a single Bullhorn candidate id.")
    ap.add_argument("--audit-csv", type=str, default=None,
                    help="Path for the audit CSV. Default: misnamed_candidates_<ts>.csv")
    args = ap.parse_args()

    audit_path = (args.audit_csv
                  or f"misnamed_candidates_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv")

    with app.app_context():
        bh_service = get_bullhorn_service()

        candidates = _find_misnamed_candidates(bh_service, args.candidate_id)
        if not candidates:
            log.info("No matching candidates found. Exiting.")
            return 0

        applied_count = 0
        skipped_count = 0
        failed_count = 0

        with open(audit_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "candidate_id", "current_first", "current_last",
                "proposed_first", "proposed_last", "applied", "note",
            ])

            for cand in candidates:
                cid = cand.get("id")
                cur_first = cand.get("firstName") or ""
                cur_last = cand.get("lastName") or ""
                log.info("Candidate %s: current name = %r %r",
                         cid, cur_first, cur_last)

                proposal = _propose_correction(bh_service, cand)
                if not proposal:
                    log.info("  -> No confident replacement available; skipping.")
                    writer.writerow([cid, cur_first, cur_last,
                                     "", "", "no", "no_valid_replacement"])
                    skipped_count += 1
                    continue

                new_first, new_last = proposal
                log.info("  -> Proposed replacement: %r %r", new_first, new_last)

                if not args.apply:
                    writer.writerow([cid, cur_first, cur_last,
                                     new_first, new_last, "no", "dry_run"])
                    continue

                try:
                    result_id = bh_service.update_candidate(cid, {
                        "firstName": new_first,
                        "lastName": new_last,
                    })
                except Exception as exc:  # pragma: no cover - defensive
                    log.exception("  -> PATCH raised for candidate %s", cid)
                    writer.writerow([cid, cur_first, cur_last,
                                     new_first, new_last, "no",
                                     f"exception: {exc}"])
                    failed_count += 1
                    continue

                if result_id is None:
                    # update_candidate logs the HTTP failure detail and
                    # returns None — record the silent-failure case so a
                    # human can investigate without re-running.
                    log.error("  -> PATCH failed (update_candidate returned None) for %s", cid)
                    writer.writerow([cid, cur_first, cur_last,
                                     new_first, new_last, "no",
                                     "update_candidate_returned_none"])
                    failed_count += 1
                    continue

                log.info("  -> PATCHed Bullhorn record %s.", cid)
                writer.writerow([cid, cur_first, cur_last,
                                 new_first, new_last, "yes", "ok"])
                applied_count += 1

    log.info("Audit CSV written to %s", audit_path)
    if args.apply:
        log.info("Apply complete: %d applied, %d skipped, %d failed.",
                 applied_count, skipped_count, failed_count)
    else:
        log.info("Dry-run complete: %d candidates evaluated, %d had no replacement.",
                 len(candidates), skipped_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
