#!/usr/bin/env python3
"""One-off repair: Cristopher Williams (4676912) false auto-merge via email:unknown.

Sep 3 2026: Scout merged 11 unrelated Archive/placeholder-email records into
Williams because email matching treated the literal string \"unknown\" as identity.

This script (run via `railway run`):
  1. Clears contaminated contact fields on Williams
  2. Soft-deletes the wrongly copied JobSubmission (Carrasco × job 33517)
  3. Soft-deletes Auto-Merge + transferred notes on Williams (keeps Dan's note)
  4. Deletes merged file copies on Williams (originals remain on source IDs)
  5. Un-archives the 10 real people; leaves Governor Kasich archived
  6. Clears email=unknown on all 11 source records + Williams

Idempotent enough to re-run: already-cleared fields / already-deleted entities
are reported and skipped.
"""
from __future__ import annotations

import json
import os
import sys
import time

WILLIAMS_ID = 4676912
KEEP_NOTE_IDS = {7255100}  # Dan Sifer — Candidate Interested on job 35750

# Notes currently sitting on Williams that came from the bad merge wave
# (Auto-Merge receipts + transferred call/prescreen/pipeline/client-sub notes).
WILLIAMS_NOTE_IDS_TO_SOFT_DELETE = [
    7255149, 7255147, 7255140, 7255133, 7255131, 7255128, 7255125,
    7255122, 7255118, 7255114, 7255110,
    7255104, 7255105, 7255106, 7255107, 7255108, 7255109,
    7255113, 7255116, 7255117, 7255120, 7255121, 7255124, 7255127,
    7255130, 7255144, 7255143, 7255145, 7255146, 7255136, 7255137,
    7255138, 7255139, 7255142, 7255135,
]

# Files tagged "Merged from candidate …" on Williams
WILLIAMS_FILE_IDS_TO_DELETE = [
    2077421,  # ROBERTO_CARRASCO.pdf
    2077423,  # Ben_GoretzkeEIT.pdf
    2077424,  # Keli_J.pdf
    2077425,  # Igor_Golubov.pdf
    2077426,  # Linkedin Photo (Natale)
    2077427,  # Linkedin Photo (Lang)
    2077428,  # resume.doc (Kasich)
]

# Carrasco Client Submission wrongly created on Williams
WILLIAMS_SUBMISSION_IDS_TO_SOFT_DELETE = [937076]

# (id, name, restore_status_or_None_to_leave_archive)
SOURCE_CANDIDATES = [
    (4578555, 'ROBERTO CARRASCO', 'Passively Looking'),
    (4574634, 'Ben Goretzke', 'Passively Looking'),
    (4564450, 'Chad Slawinski', 'Passively Looking'),
    (4562931, 'Jeff MacCubbin', 'Passively Looking'),
    (4560178, 'Keli J.', 'Passively Looking'),
    (4556044, 'Jenny He', 'Passively Looking'),
    (4379555, 'Igor Golubov', 'Passively Looking'),
    (4227539, 'Chris Masteller', 'Passively Looking'),
    (4223526, 'Tony Natale', 'Passively Looking'),
    (4223522, 'Christopher Lang', 'Passively Looking'),
    (4214361, 'Governor Kasich', None),  # leave Archive
]


def main() -> int:
    # Avoid importing Flask app (needs Railway-internal Postgres). Bullhorn
    # credentials alone are enough for this repair.
    from bullhorn_service import BullhornService

    report = {
        'williams_fields': {},
        'notes_soft_deleted': [],
        'notes_failed': [],
        'files_deleted': [],
        'files_failed': [],
        'submissions_soft_deleted': [],
        'submissions_failed': [],
        'sources_restored': [],
        'sources_email_cleared': [],
        'sources_failed': [],
    }

    # Pass env credentials explicitly so BullhornService skips DB GlobalSettings
    # (which requires Railway-internal Postgres not reachable from local).
    bh = BullhornService(
        client_id=os.environ.get('BULLHORN_CLIENT_ID') or os.environ.get('BH_CLIENT_ID'),
        client_secret=os.environ.get('BULLHORN_CLIENT_SECRET') or os.environ.get('BH_CLIENT_SECRET'),
        username=os.environ.get('BULLHORN_USERNAME') or os.environ.get('BH_USERNAME'),
        password=os.environ.get('BULLHORN_PASSWORD') or os.environ.get('BH_PASSWORD'),
    )
    if not all([bh.client_id, bh.client_secret, bh.username, bh.password]):
        print('ERROR: Bullhorn credentials missing from environment', file=sys.stderr)
        return 1
    if not bh.authenticate():
        print('ERROR: Bullhorn authentication failed', file=sys.stderr)
        return 1

    # ── 1. Clear Williams contact contamination ──────────────────────────
    ok = bh.update_candidate(WILLIAMS_ID, {
        'email': '',
        'phone': '',
        'mobile': '',
    })
    report['williams_fields'] = {
        'updated': bool(ok),
        'cleared': ['email', 'phone', 'mobile'],
    }
    print(f"Williams {WILLIAMS_ID} contact clear: {ok}")

    # ── 2. Soft-delete wrong JobSubmission copy ──────────────────────────
    for sid in WILLIAMS_SUBMISSION_IDS_TO_SOFT_DELETE:
        try:
            success = bh.update_entity('JobSubmission', sid, {'isDeleted': True})
            if success:
                report['submissions_soft_deleted'].append(sid)
                print(f"  Soft-deleted JobSubmission {sid}")
            else:
                report['submissions_failed'].append(sid)
                print(f"  FAILED soft-delete JobSubmission {sid}")
        except Exception as exc:
            report['submissions_failed'].append(sid)
            print(f"  FAILED soft-delete JobSubmission {sid}: {exc}")
        time.sleep(0.2)

    # ── 3. Soft-delete contaminated notes on Williams ─────────────────────
    for nid in WILLIAMS_NOTE_IDS_TO_SOFT_DELETE:
        if nid in KEEP_NOTE_IDS:
            continue
        try:
            url = f"{bh.base_url}entity/Note/{nid}"
            params = {'BhRestToken': bh.rest_token}
            resp = bh.session.post(
                url, params=params, json={'isDeleted': True}, timeout=30
            )
            if resp.status_code == 401:
                bh.rest_token = None
                if bh.authenticate():
                    params['BhRestToken'] = bh.rest_token
                    resp = bh.session.post(
                        url, params=params, json={'isDeleted': True}, timeout=30
                    )
            if resp.status_code in (200, 201):
                report['notes_soft_deleted'].append(nid)
            else:
                report['notes_failed'].append({
                    'id': nid,
                    'status': resp.status_code,
                    'body': (resp.text or '')[:200],
                })
                print(f"  FAILED note {nid}: HTTP {resp.status_code}")
        except Exception as exc:
            report['notes_failed'].append({'id': nid, 'error': str(exc)})
            print(f"  FAILED note {nid}: {exc}")
        time.sleep(0.15)
    print(
        f"Notes soft-deleted: {len(report['notes_soft_deleted'])} "
        f"failed={len(report['notes_failed'])}"
    )

    # ── 4. Delete merged file copies on Williams ──────────────────────────
    for fid in WILLIAMS_FILE_IDS_TO_DELETE:
        try:
            # Bullhorn file API: DELETE /file/Candidate/{candidateId}/{fileId}
            url = f"{bh.base_url}file/Candidate/{WILLIAMS_ID}/{fid}"
            params = {'BhRestToken': bh.rest_token}
            resp = bh.session.delete(url, params=params, timeout=30)
            if resp.status_code == 401:
                bh.rest_token = None
                if bh.authenticate():
                    params['BhRestToken'] = bh.rest_token
                    resp = bh.session.delete(url, params=params, timeout=30)
            if resp.status_code in (200, 204):
                report['files_deleted'].append(fid)
                print(f"  Deleted file {fid} from Williams")
            else:
                report['files_failed'].append({
                    'id': fid,
                    'status': resp.status_code,
                    'body': (resp.text or '')[:200],
                })
                print(f"  FAILED file {fid}: HTTP {resp.status_code} {(resp.text or '')[:120]}")
        except Exception as exc:
            report['files_failed'].append({'id': fid, 'error': str(exc)})
            print(f"  FAILED file {fid}: {exc}")
        time.sleep(0.2)

    # ── 5/6. Restore sources + clear placeholder email ────────────────────
    for cid, name, restore_status in SOURCE_CANDIDATES:
        try:
            payload = {'email': ''}
            if restore_status:
                payload['status'] = restore_status
            ok = bh.update_candidate(cid, payload)
            if ok:
                report['sources_email_cleared'].append(cid)
                if restore_status:
                    report['sources_restored'].append({
                        'id': cid, 'name': name, 'status': restore_status,
                    })
                    print(f"  Restored {name} ({cid}) → {restore_status}, cleared email")
                else:
                    print(f"  Left {name} ({cid}) archived, cleared email")
            else:
                report['sources_failed'].append({'id': cid, 'name': name})
                print(f"  FAILED update {name} ({cid})")
        except Exception as exc:
            report['sources_failed'].append({
                'id': cid, 'name': name, 'error': str(exc),
            })
            print(f"  FAILED update {name} ({cid}): {exc}")
        time.sleep(0.25)

    # ── Verify Williams ───────────────────────────────────────────────────
    verified = bh.get_candidate(WILLIAMS_ID) or {}
    report['williams_after'] = {
        'id': WILLIAMS_ID,
        'email': verified.get('email'),
        'phone': verified.get('phone'),
        'mobile': verified.get('mobile'),
        'status': verified.get('status'),
        'owner': verified.get('owner'),
    }

    print('\n=== REPAIR REPORT ===')
    print(json.dumps(report, indent=2, default=str))
    return 0 if not (
        report['notes_failed']
        or report['files_failed']
        or report['submissions_failed']
        or report['sources_failed']
    ) else 2


if __name__ == '__main__':
    sys.exit(main())
