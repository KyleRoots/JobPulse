"""
Candidate Name Extraction Helpers

Deterministic helpers used by the inbound email parser as fallbacks when
the primary subject/body regex and the AI resume parser both fail to
recover a candidate name.

The goal of this module is "no silent drops": when standard board emails
arrive in unusual formats (3+ token names, hyphenated names, name
particles, mixed casing, generic resume filenames) we still want a
deterministic chance to extract the candidate's name before falling
through to the last-resort AI call or admin notification.

All public helpers are pure functions (no I/O, no DB, no network).
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du",
    "la", "le", "el", "al", "bin", "ben", "ibn", "abu", "mac", "mc",
    "san", "santa", "st", "st.", "ter", "ten", "los", "las", "do",
    "dos", "das",
}

GENERIC_FILENAME_TOKENS = {
    "resume", "cv", "curriculum", "vitae", "profile", "candidate",
    "applicant", "application", "updated", "new", "final", "latest",
    "current", "doc", "document", "file", "copy", "untitled",
    "myresume", "my", "personal",
}

INVALID_NAME_TOKENS = GENERIC_FILENAME_TOKENS | {
    "none", "null", "n/a", "na", "unknown", "anonymous", "test",
    "candidate", "applicant",
}

# Tokens that, when present in a (first, last) pair, indicate a
# work-authorization or citizenship phrase rather than a real candidate
# name. Production failure: "Canadian Citizen" was extracted from a
# resume header line below the actual name and shipped to Bullhorn as
# firstName="Canadian" / lastName="Citizen". Any name containing one of
# these tokens (case-insensitive, exact whitespace/hyphen-split match)
# is rejected by ``is_valid_name``.
#
# Tokens here MUST be unambiguously work-authorization vocabulary —
# words that are essentially zero-probability as a person's legal name
# token. Ambiguous words like "Green" (a real surname — Eva Green, John
# Green) and "Permanent" are intentionally NOT in this set; they are
# only rejected when they appear in a known work-auth phrase, see
# :data:`WORK_AUTH_PHRASES` below.
WORK_AUTH_TOKENS = {
    "citizen", "citizens", "citizenship",
    "resident", "residency", "residents",
    "naturalized",
    "visa", "visas",
    "h1b", "h-1b", "h1-b",
    "ead", "opt", "cpt",
    "asylee", "asylum", "refugee",
}

# Multi-word work-authorization phrases. Matched as a substring inside
# the lowercased, whitespace-normalized "first last" string so that
# ambiguous single tokens like "green" and "permanent" still trigger
# rejection in their work-auth form ("green card", "permanent resident")
# without rejecting legitimate surnames like "Green" or "Permanent".
WORK_AUTH_PHRASES = {
    "green card",
    "green card holder",
    "permanent resident",
    "permanent residency",
    "permanent residents",
    "work permit",
    "work authorization",
    "work authorized",
    "work eligible",
    "authorized to work",
    "eligible to work",
    "right to work",
    "lawful permanent",
}

NAME_TOKEN_RE = r"[A-Za-z][A-Za-z'\-]*"
# Non-greedy multi-token name capture so trailing suffix anchors match
# correctly. Allows 1-5 additional tokens after the first.
MULTI_TOKEN_NAME_PATTERN = rf"({NAME_TOKEN_RE}(?:\s+{NAME_TOKEN_RE}){{1,5}}?)"


def is_valid_name_token(token: str) -> bool:
    """Return True if a single token plausibly belongs to a person's name."""
    if not token:
        return False
    cleaned = token.strip().lower().rstrip(".")
    if not cleaned:
        return False
    if cleaned in INVALID_NAME_TOKENS:
        return False
    if not re.match(r"^[a-z][a-z'\-]*$", cleaned):
        return False
    if len(cleaned) > 40:
        return False
    return True


def is_work_auth_phrase(text: Optional[str]) -> bool:
    """Return True if ``text`` is a work-authorization / citizenship phrase.

    Two-layer match:

    1. **Single-token rule** — tokenises on whitespace and hyphens
       (case-insensitive) and rejects if any token appears in
       :data:`WORK_AUTH_TOKENS`. These tokens are unambiguously
       work-auth vocabulary (``"citizen"``, ``"visa"``, ``"h1b"`` …)
       so an exact match is safe.

    2. **Phrase rule** — also checks if any phrase in
       :data:`WORK_AUTH_PHRASES` appears as a substring in the
       lowercased, whitespace-normalised text. This catches phrases
       built from ambiguous tokens (``"green card"``,
       ``"permanent resident"``) without rejecting the bare surname
       form (``"Eva Green"``, ``"John Permanent"``).

    Used as a blocklist by :func:`is_valid_name` and by upstream
    extractors so a line like ``"Canadian Citizen"`` or
    ``"Permanent Resident"`` can never be committed as a candidate's
    first/last name.
    """
    if not text:
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    # Single-token rule: split on whitespace AND hyphens so "h-1b" and
    # "green-card" both decompose correctly.
    tokens = re.split(r"[\s\-]+", cleaned)
    if any(tok.strip(".,") in WORK_AUTH_TOKENS for tok in tokens):
        return True
    # Phrase rule: substring match after collapsing whitespace.
    normalised = " ".join(cleaned.split())
    for phrase in WORK_AUTH_PHRASES:
        if phrase in normalised:
            return True
    return False


def is_valid_name(first_name: Optional[str], last_name: Optional[str]) -> bool:
    """Return True if the (first, last) pair looks like a real person name.

    Rejects: empty/None, generic placeholders ("None None", "Resume Doc"),
    work-authorization / citizenship phrases ("Canadian Citizen",
    "Permanent Resident", "H1B Visa"), or anything containing
    digits/symbols other than hyphens and apostrophes. Multi-word last
    names are supported by validating each whitespace-separated token
    individually (e.g. "El Fared", "van der Berg") — name particles
    count as valid tokens.
    """
    if not first_name or not last_name:
        return False
    first = first_name.strip()
    last = last_name.strip()
    if not first or not last:
        return False

    # Reject work-authorization / citizenship phrases anywhere in the
    # combined name. This blocks the production failure mode where a
    # parser picked up a "Canadian Citizen" header line below the actual
    # name and shipped it to Bullhorn as the candidate's name.
    combined = f"{first} {last}"
    if is_work_auth_phrase(combined):
        return False

    # Validate first name: must be a single valid token
    if not is_valid_name_token(first):
        return False

    # Validate last name: split on whitespace, every token must be either
    # a known particle or a valid name token. At least one non-particle
    # token must exist (otherwise it's just particles like "van der").
    last_tokens = last.split()
    if not last_tokens:
        return False
    has_real_surname = False
    for tok in last_tokens:
        if tok.lower().rstrip(".") in NAME_PARTICLES:
            continue
        if not is_valid_name_token(tok):
            return False
        has_real_surname = True
    return has_real_surname


def split_full_name(full_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Split a full-name string into (first_name, last_name).

    Handles:
      - 2 tokens:                "John Smith"          -> (John, Smith)
      - 3+ tokens:               "Abderrahmane El Fared" -> (Abderrahmane, El Fared)
      - particles in last name:  "John van der Berg"   -> (John, van der Berg)
      - hyphenated:              "Mary-Jane O'Brien"   -> (Mary-Jane, O'Brien)
      - "Last, First" comma:     "Smith, John"         -> (John, Smith)
      - mononym (single token):  "Madonna"             -> (Madonna, None)

    Returns (None, None) if the input is empty or non-alphabetic noise.
    """
    if not full_name:
        return None, None

    cleaned = re.sub(r"\s+", " ", full_name.strip())
    if not cleaned:
        return None, None

    # "Last, First [Middle]" convention
    if "," in cleaned:
        last_part, _, first_part = cleaned.partition(",")
        last_part = last_part.strip()
        first_part = first_part.strip()
        if last_part and first_part:
            first_tokens = first_part.split()
            return _titlecase(first_tokens[0]), _titlecase(last_part)

    tokens = cleaned.split()
    tokens = [t for t in tokens if re.match(r"^[A-Za-z][A-Za-z'\-]*$", t)]
    if not tokens:
        return None, None

    if len(tokens) == 1:
        return _titlecase(tokens[0]), None

    first = tokens[0]
    last_tokens = tokens[1:]

    # Collapse trailing particles into the last name (e.g. "van der Berg")
    last = " ".join(_format_last_name_token(t) for t in last_tokens)
    return _titlecase(first), last.strip() or None


def _titlecase(token: str) -> str:
    """Normalize casing for a single name token preserving hyphens/apostrophes."""
    if not token:
        return token

    def cap(part: str) -> str:
        if not part:
            return part
        return part[:1].upper() + part[1:].lower()

    parts = re.split(r"([\-'])", token)
    return "".join(cap(p) if p not in ("-", "'") else p for p in parts)


def _format_last_name_token(token: str) -> str:
    """Lowercase known particles, title-case real surnames."""
    if token.lower().rstrip(".") in NAME_PARTICLES:
        return token.lower()
    return _titlecase(token)


def extract_name_from_pattern(
    text: str,
    prefix_pattern: str,
    suffix_pattern: str = r"(?=\s+has\s+applied|\s+applied\s+|\s*$|\s*[\n\r])",
) -> Tuple[Optional[str], Optional[str]]:
    """Search ``text`` for ``prefix_pattern`` followed by a multi-token name.

    ``prefix_pattern`` is a raw regex fragment that should leave the cursor
    immediately before the name to capture (e.g. r"-\\s*" or r"Name[:\\s]+").

    ``suffix_pattern`` defaults to a lookahead that stops at common job-board
    boundaries ("has applied", "applied", end-of-line). Pass an empty string
    to disable suffix anchoring.

    The non-greedy multi-token quantifier guarantees that the first valid
    sentence boundary wins — preventing capture from absorbing trailing
    "has applied on LinkedIn" text into the surname.

    Returns (first_name, last_name) or (None, None) on no match.
    """
    if not text:
        return None, None
    # Truncate adversarially long input before regex search to bound any
    # backtracking work. 20 KB is well above the largest realistic email
    # subject + label-fragment we extract from.
    if len(text) > 20_000:
        text = text[:20_000]
    pattern_str = prefix_pattern + MULTI_TOKEN_NAME_PATTERN + suffix_pattern
    try:
        pattern = re.compile(pattern_str, re.IGNORECASE)
    except re.error:
        return None, None
    match = pattern.search(text)
    if not match:
        return None, None
    captured = match.group(1)
    return split_full_name(captured)


def parse_name_from_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of a candidate name from a resume filename.

    Handles common conventions:
      - "First Last Resume.docx"
      - "First_Last_CV.pdf"
      - "Last, First - Resume 2024.pdf"
      - "Resume - First Last.pdf"
      - "First-Last_resume_v2.docx"

    Strips file extensions, suffix tokens (Resume, CV, year tags, version
    numbers), and replaces underscores/hyphens with spaces before
    delegating to ``split_full_name``.
    """
    if not filename:
        return None, None

    base = os.path.basename(filename)
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)  # strip extension

    # Normalise separators: underscores and dots become spaces; preserve
    # internal hyphens and apostrophes for names like "Mary-Jane".
    normalised = re.sub(r"[_\.]+", " ", base)
    normalised = re.sub(r"\s+-\s+", " ", normalised)

    # Drop generic resume/CV/version/year suffixes anywhere in the name.
    tokens = normalised.split()
    filtered = []
    for tok in tokens:
        bare = tok.lower().strip("()[]{}")
        if bare in GENERIC_FILENAME_TOKENS:
            continue
        if re.fullmatch(r"v\d+(\.\d+)?", bare):  # version tags v1, v2.0
            continue
        if re.fullmatch(r"\d{4}", bare):  # year tags 2024
            continue
        if re.fullmatch(r"\d+", bare):  # plain numbers
            continue
        filtered.append(tok)

    if not filtered:
        return None, None

    candidate = " ".join(filtered)
    first, last = split_full_name(candidate)

    # If the first or last token is a generic filename word that slipped
    # through (e.g. "Resume Smith"), reject the result.
    if first and first.lower() in INVALID_NAME_TOKENS:
        return None, None
    if last and last.split()[0].lower() in INVALID_NAME_TOKENS:
        return None, None

    return first, last


def parse_name_from_email_address(email: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract a (first, last) guess from local-part of an email address.

    Conventions handled:
      - first.last@domain    -> (First, Last)
      - first_last@domain    -> (First, Last)
      - firstlast@domain     -> (None, None)  -- ambiguous, do not guess
      - flast@domain         -> (None, None)
      - first-last@domain    -> (First, Last)

    Numbers and trailing digits are stripped (e.g. ``john.smith24@``).
    """
    if not email or "@" not in email:
        return None, None
    local = email.split("@", 1)[0]
    local = re.sub(r"\d+$", "", local)
    parts = re.split(r"[._\-]+", local)
    parts = [p for p in parts if p and p.isalpha() and len(p) >= 2]
    if len(parts) < 2:
        return None, None
    return _titlecase(parts[0]), _titlecase(parts[-1])


def merge_name_candidates(*candidates: Tuple[Optional[str], Optional[str]]) -> Tuple[Optional[str], Optional[str]]:
    """Pick the first (first, last) tuple where both halves look valid.

    Used to combine results from email-subject extraction, AI resume
    parsing, filename parsing, and the last-resort AI call into a single
    decision.
    """
    for first, last in candidates:
        if is_valid_name(first, last):
            return first, last
    # Accept partial result (first only) only if no full pair was found.
    for first, last in candidates:
        if first and is_valid_name_token(first):
            return first, last
    return None, None


def strip_html_to_text(html: str) -> str:
    """Convert HTML email body to a plain-text approximation for regex.

    Preserves label structure ("Email:", "Phone:") that the existing
    regex relies on, and inserts whitespace around block-level tags so
    adjacent fields don't collide.
    """
    if not html:
        return ""
    if "<" not in html:
        return html
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Replace <br> with newlines so phone/email labels stay on their own line
        for br in soup.find_all("br"):
            br.replace_with("\n")
        # Add newlines around block elements (also include td and h5/h6)
        for tag in soup.find_all(["p", "div", "tr", "td", "li",
                                   "h1", "h2", "h3", "h4", "h5", "h6"]):
            tag.insert_before("\n")
            tag.insert_after("\n")
        # Add a single-space separator around inline tags so adjacent
        # spans like "<span>Name</span><span>John Doe</span>" don't
        # collide into "NameJohn Doe".
        for tag in soup.find_all(["span", "strong", "b", "em", "i", "label"]):
            tag.insert_before(" ")
            tag.insert_after(" ")
        text = soup.get_text(separator=" ", strip=False)
    except Exception:
        # Defensive fallback if BeautifulSoup misbehaves on malformed HTML
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|tr|td|li|h\d)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?(span|strong|b|em|i|label)>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\u00a0", " ", text)  # non-breaking spaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_extraction_summary(resume_data: Dict, email_candidate: Dict, filename: Optional[str]) -> Dict:
    """Compose a structured snapshot of everything we *did* manage to extract.

    Used by the admin notification path so a human can pick up where the
    automation gave up without re-doing the parsing work.
    """
    return {
        "filename": filename,
        "email_extracted": {
            "first_name": email_candidate.get("first_name"),
            "last_name": email_candidate.get("last_name"),
            "email": email_candidate.get("email"),
            "phone": email_candidate.get("phone"),
        },
        "resume_extracted": {
            "first_name": resume_data.get("first_name"),
            "last_name": resume_data.get("last_name"),
            "email": resume_data.get("email"),
            "phone": resume_data.get("phone"),
            "current_title": resume_data.get("current_title"),
            "current_company": resume_data.get("current_company"),
            "years_experience": resume_data.get("years_experience"),
            "skills_count": len(resume_data.get("skills") or []),
            "skills_preview": (resume_data.get("skills") or [])[:10],
            "city": resume_data.get("city"),
            "state": resume_data.get("state"),
        },
    }
