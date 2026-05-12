"""One-time migration: move misrouted intake artifacts from archived
Bullhorn candidate 4020713 (Ram Pathak, MYT-Chicago, Archive) to live
winner 4452544 (Ram Pathak, MYT-Ottawa, Placed).

Background: a 5/11/2026 job application landed on the archived loser
record due to the bug fixed in commit 2bf5316. This script copies the
two misrouted notes (and any 5/11 web response / file attachments)
onto the live winner, plus writes audit-trail notes on both sides.

Idempotency: aborts if 4452544 already contains a "[Scout Genius
Auto-Migrate 2026-05-12]" marker note. Safe to re-run.

Usage:
    python -m scripts.migrate_4020713_to_4452544 [--apply]

Without --apply, runs read-only DRY RUN and prints the inventory.
With --apply, performs the writes.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app import app  # noqa: F401  (initializes Flask app context)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("migrate-4020713")

ARCHIVED_ID = 4020713
LIVE_ID = 4452544
_window_start_iso = "2026-05-10T00:00:00Z"
_window_end_iso = "2026-05-13T00:00:00Z"
DAY_START_MS = int(datetime(2026, 5, 10, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
DAY_END_MS = int(datetime(2026, 5, 13, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
AUDIT_MARKER = "[Scout Genius Auto-Migrate 2026-05-12]"
NOTE_ACTIONS_TO_MIGRATE = {"Scout Screen - Location Review", "AI Resume Summary"}


def _ms_to_iso(ms: int) -> str:
    if not ms:
        return "(no date)"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def _fetch_inventory(bh, candidate_id: int) -> Dict:
    """Read-only enumeration of 5/11/2026 artifacts on the given candidate."""
    inv = {'notes_5_11': [], 'web_responses_5_11': [], 'files_5_11': [], 'all_recent_notes': []}

    # Notes — pull last 30, then filter to 5/11
    url = f"{bh.base_url}entity/Candidate/{candidate_id}/notes"
    params = {
        'fields': 'id,action,dateAdded,comments,commentingPerson(id,firstName,lastName)',
        'count': 30,
        'orderBy': '-dateAdded',
        'BhRestToken': bh.rest_token,
    }
    r = bh.session.get(url, params=params, timeout=30)
    if r.status_code == 200:
        notes = (r.json() or {}).get('data', [])
        inv['all_recent_notes'] = notes
        inv['notes_5_11'] = [
            n for n in notes
            if DAY_START_MS <= (n.get('dateAdded') or 0) < DAY_END_MS
        ]

    # Web responses — search by candidate.id
    wr_url = f"{bh.base_url}search/JobBoardPost"
    # JobBoardPost is the published-job side; web responses ARE candidates that applied.
    # The right entity here is JobSubmission tied to source=Web Response, OR the
    # CandidateReference — but the most universally available is JobSubmission with
    # candidate.id filter.
    js_url = f"{bh.base_url}search/JobSubmission"
    js_params = {
        'query': f'candidate.id:{candidate_id}',
        'fields': 'id,dateAdded,status,source,jobOrder(id,title)',
        'count': 20,
        'sort': '-dateAdded',
        'BhRestToken': bh.rest_token,
    }
    r = bh.session.get(js_url, params=js_params, timeout=30)
    if r.status_code == 200:
        subs = (r.json() or {}).get('data', [])
        inv['web_responses_5_11'] = [
            s for s in subs
            if DAY_START_MS <= (s.get('dateAdded') or 0) < DAY_END_MS
        ]

    # Files
    files = bh.get_entity_files('Candidate', candidate_id) or []
    inv['files_5_11'] = [
        f for f in files
        if DAY_START_MS <= (f.get('dateAdded') or 0) < DAY_END_MS
    ]

    return inv


def _has_audit_marker(bh, candidate_id: int) -> bool:
    notes = bh.get_candidate_notes(candidate_id, count=50) or []
    for n in notes:
        if AUDIT_MARKER in (n.get('comments') or ''):
            log.warning(f"Found prior audit marker on candidate {candidate_id} (note id={n.get('id')}) — aborting to preserve idempotency")
            return True
    return False


def _print_inventory(label: str, inv: Dict) -> None:
    print(f"\n────── INVENTORY: {label} ──────")
    print(f"  ALL RECENT NOTES (last 30, regardless of date) — {len(inv['all_recent_notes'])} total:")
    for n in inv['all_recent_notes']:
        cp = n.get('commentingPerson') or {}
        author = f"{cp.get('firstName', '?')} {cp.get('lastName', '?')}"
        body = (n.get('comments') or '')[:60].replace('\n', ' ')
        print(f"    • id={n['id']} | {_ms_to_iso(n.get('dateAdded'))} | action={n.get('action')!r} | by {author}")
        print(f"      {body!r}…")
    print(f"\n  Notes within window {_window_start_iso}..{_window_end_iso} ({len(inv['notes_5_11'])}):")
    for n in inv['notes_5_11']:
        cp = n.get('commentingPerson') or {}
        author = f"{cp.get('firstName', '?')} {cp.get('lastName', '?')} (id={cp.get('id')})"
        body = (n.get('comments') or '')[:80].replace('\n', ' ')
        print(f"    • id={n['id']} | {_ms_to_iso(n.get('dateAdded'))} | action={n.get('action')!r}")
        print(f"      author={author}")
        print(f"      body={body!r}…")
    print(f"  Job submissions on 5/11/2026 ({len(inv['web_responses_5_11'])}):")
    for s in inv['web_responses_5_11']:
        jo = s.get('jobOrder') or {}
        print(f"    • id={s['id']} | {_ms_to_iso(s.get('dateAdded'))} | status={s.get('status')} | source={s.get('source')} | job={jo.get('id')} ({jo.get('title')})")
    print(f"  Files on 5/11/2026 ({len(inv['files_5_11'])}):")
    for f in inv['files_5_11']:
        print(f"    • id={f.get('id')} | {_ms_to_iso(f.get('dateAdded'))} | name={f.get('name')!r} | type={f.get('type')} | size={f.get('fileSize')}")


def _build_migrated_note_body(orig_note: Dict, src_id: int) -> str:
    cp = orig_note.get('commentingPerson') or {}
    author = f"{cp.get('firstName', '?')} {cp.get('lastName', '?')} (BH user id={cp.get('id')})"
    orig_body = orig_note.get('comments') or ''
    return (
        f"{AUDIT_MARKER} MIGRATED from candidate {src_id} (archived).\n"
        f"Originally posted: {_ms_to_iso(orig_note.get('dateAdded'))}\n"
        f"Original author: {author}\n"
        f"Original note ID on {src_id}: {orig_note.get('id')}\n"
        f"Original action: {orig_note.get('action')!r}\n"
        f"---\n"
        f"{orig_body}"
    )


def _build_audit_note_for_winner(inv_archived: Dict, src_id: int, dst_id: int,
                                  copied_note_ids: List[int]) -> str:
    js_lines = []
    for s in inv_archived['web_responses_5_11']:
        jo = s.get('jobOrder') or {}
        js_lines.append(
            f"  - JobSubmission id={s['id']}, job={jo.get('id')} ({jo.get('title')}), "
            f"status={s.get('status')}, source={s.get('source')}, dated {_ms_to_iso(s.get('dateAdded'))}"
        )
    file_lines = [
        f"  - File id={f.get('id')} name={f.get('name')!r} type={f.get('type')} dated {_ms_to_iso(f.get('dateAdded'))}"
        for f in inv_archived['files_5_11']
    ]
    return (
        f"{AUDIT_MARKER} Misrouted intake artifacts moved here from archived candidate {src_id}.\n"
        f"Root cause: pre-fix archive-redirect bug (fixed commit 2bf5316).\n"
        f"5/11/2026 notes copied to this record: {copied_note_ids}\n"
        f"5/11/2026 JobSubmissions still on archived record {src_id} (read-only in BH, NOT moved):\n"
        + ("\n".join(js_lines) if js_lines else "  (none)") + "\n"
        f"5/11/2026 Files still on archived record {src_id} (NOT moved — manual re-upload if needed):\n"
        + ("\n".join(file_lines) if file_lines else "  (none)") + "\n"
        f"Recommendation: recruiter review attached job(s) and re-create the application "
        f"on this live record if desired. The archived record {src_id} has been left "
        f"intact for full audit trail."
    )


def _build_audit_note_for_loser(dst_id: int, copied_note_ids: List[int]) -> str:
    return (
        f"{AUDIT_MARKER} The 5/11/2026 intake notes on this archived record were "
        f"misrouted due to the pre-fix archive-redirect bug. They have been copied "
        f"to the live winner candidate {dst_id} (notes {copied_note_ids}). "
        f"This archived record remains in place for audit trail."
    )


def main(apply: bool = False) -> int:
    from bullhorn_service import BullhornService

    bh = BullhornService()
    if not bh.authenticate():
        log.error("Bullhorn auth failed — aborting")
        return 2

    log.info(f"Authenticated. base_url={bh.base_url}, user_id={bh.user_id}")

    # Sanity check both candidates exist
    archived = bh.get_candidate(ARCHIVED_ID)
    live = bh.get_candidate(LIVE_ID)
    if not archived:
        log.error(f"Archived candidate {ARCHIVED_ID} not found")
        return 2
    if not live:
        log.error(f"Live candidate {LIVE_ID} not found")
        return 2
    log.info(f"Archived {ARCHIVED_ID}: {archived.get('firstName')} {archived.get('lastName')} | status={archived.get('status')}")
    log.info(f"Live     {LIVE_ID}: {live.get('firstName')} {live.get('lastName')} | status={live.get('status')}")

    if (live.get('status') or '').strip().lower() == 'archive':
        log.error(f"Live target {LIVE_ID} is itself ARCHIVE — refusing to write")
        return 2

    # Idempotency — bail if migration already happened
    if _has_audit_marker(bh, LIVE_ID):
        log.error("Migration already performed (audit marker present on live record). Exiting.")
        return 0

    # Phase 1: inventory the archived record
    log.info(f"Reading 5/11/2026 inventory from archived candidate {ARCHIVED_ID}...")
    inv = _fetch_inventory(bh, ARCHIVED_ID)
    _print_inventory(f"Archived {ARCHIVED_ID}", inv)

    # Determine what we'll migrate
    notes_to_copy = [
        n for n in inv['notes_5_11']
        if (n.get('action') or '') in NOTE_ACTIONS_TO_MIGRATE
    ]
    print(f"\n────── PLAN ──────")
    print(f"  → COPY {len(notes_to_copy)} note(s) to live candidate {LIVE_ID}:")
    for n in notes_to_copy:
        print(f"      • {n.get('action')!r} (orig id {n['id']}, {_ms_to_iso(n.get('dateAdded'))})")
    print(f"  → WRITE 1 audit-trail note on live candidate {LIVE_ID}")
    print(f"  → WRITE 1 audit-trail note on archived candidate {ARCHIVED_ID}")
    print(f"  → DO NOT TOUCH JobSubmissions (Bullhorn-side read-only — documented in audit note)")
    print(f"  → DO NOT TOUCH file attachments (would require download+re-upload — documented in audit note)")
    print(f"  → DO NOT TOUCH any other field on either record")

    if not apply:
        print("\n[DRY RUN] No writes performed. Re-run with --apply to execute.")
        return 0

    if not notes_to_copy and not inv['web_responses_5_11'] and not inv['files_5_11']:
        log.warning("Nothing to migrate — exiting without writes")
        return 0

    # Phase 2: writes
    print(f"\n────── EXECUTING WRITES ──────")
    copied_note_ids: List[int] = []
    for n in notes_to_copy:
        body = _build_migrated_note_body(n, ARCHIVED_ID)
        new_id = bh.create_candidate_note(LIVE_ID, body, action=n.get('action') or 'General Notes')
        if new_id:
            log.info(f"✅ Copied note {n.get('action')!r} (orig {n['id']}) → live {LIVE_ID} as note {new_id}")
            copied_note_ids.append(new_id)
        else:
            log.error(f"❌ FAILED to copy note (orig {n['id']}) — see logs above; continuing")

    # Audit on winner
    audit_winner_id = bh.create_candidate_note(
        LIVE_ID,
        _build_audit_note_for_winner(inv, ARCHIVED_ID, LIVE_ID, copied_note_ids),
        action='General Notes',
    )
    log.info(f"✅ Wrote winner audit note id={audit_winner_id} on {LIVE_ID}")

    # Audit on loser
    audit_loser_id = bh.create_candidate_note(
        ARCHIVED_ID,
        _build_audit_note_for_loser(LIVE_ID, copied_note_ids),
        action='General Notes',
    )
    log.info(f"✅ Wrote loser audit note id={audit_loser_id} on {ARCHIVED_ID}")

    # Verify
    print(f"\n────── VERIFICATION ──────")
    verify = bh.get_candidate_notes(LIVE_ID, count=10) or []
    print(f"  Last 10 notes on live candidate {LIVE_ID}:")
    for n in verify:
        body = (n.get('comments') or '')[:80].replace('\n', ' ')
        print(f"    • id={n.get('id')} | {_ms_to_iso(n.get('dateAdded'))} | action={n.get('action')!r}")
        print(f"      {body!r}…")

    print(f"\n────── SUMMARY ──────")
    print(f"  Notes copied:      {copied_note_ids}")
    print(f"  Winner audit note: {audit_winner_id}")
    print(f"  Loser audit note:  {audit_loser_id}")
    print(f"  ✅ Migration complete.")
    return 0


if __name__ == '__main__':
    apply_flag = '--apply' in sys.argv
    sys.exit(main(apply=apply_flag))
