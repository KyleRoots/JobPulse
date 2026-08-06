#!/usr/bin/env python3
"""Repair Zip Easy Apply applicants collapsed onto Bullhorn Candidate 4380273.

Root cause (fixed Jul 26): Zip bodies greet ``Hi apply@myticas.com``; that address
was used as candidate email and email-deduped onto the junk record that owns apply@.

This script:
  1. Re-parses each collapsed PE's résumé (from the file on 4380273)
  2. Creates (or finds by real email/phone) a proper Bullhorn candidate
  3. Uploads the résumé and creates the job submission
  4. Soft-deletes the mis-attributed JobSubmission on 4380273
  5. Updates ParsedEmail to point at the new candidate
  6. Clears screening state so Scout re-vets like a normal LinkedIn inbound
  7. Neutralizes 4380273's apply@ email so it cannot collapse again
  8. Soft-deletes remaining Zip-era submissions on 4380273 (keeps original 620809)

Usage:
  DRY_RUN=1 railway run ... python scripts/repair_zip_apply_collapse.py
  railway run ... python scripts/repair_zip_apply_collapse.py
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
# Quiet noisy boot
for name in (
    'extensions', 'seeding', 'apscheduler', 'bullhorn_service.jobs',
    'httpx', 'openai',
):
    logging.getLogger(name).setLevel(logging.WARNING)

logger = logging.getLogger('repair_zip_collapse')

JUNK_CANDIDATE_ID = 4380273
# Original legitimate submission on the ISMS SME record — do not soft-delete.
KEEP_SUBMISSION_ID = 620809
SOURCE = 'ZipRecruiter Job Board'


def _is_junk_contact(email: Optional[str], phone: Optional[str]) -> Tuple[bool, bool]:
    from utils.candidate_name_extraction import (
        is_job_board_relay_email,
        is_owned_intake_mailbox,
    )
    email_bad = True
    if email and str(email).strip():
        e = str(email).strip().lower()
        email_bad = (
            is_job_board_relay_email(e)
            or is_owned_intake_mailbox(e)
            or e in ('apply@myticas.com', 'noreply@ziprecruiter.com')
        )
    phone_digits = re.sub(r'\D', '', phone or '')
    # Junk candidate's phone was contaminating some PE rows
    phone_bad = phone_digits in ('', '4044443953')
    return email_bad, phone_bad


def _download_resume(bh, candidate_id: int, file_id: int) -> Optional[Tuple[bytes, str]]:
    import requests
    url = f"{bh.base_url}file/Candidate/{candidate_id}/{file_id}"
    resp = requests.get(url, params={'BhRestToken': bh.rest_token}, timeout=60)
    if resp.status_code != 200:
        logger.warning(f"download file {file_id} failed: {resp.status_code}")
        return None
    content = resp.content
    filename = f'resume_{file_id}.pdf'
    if content and content.lstrip()[:1] == b'{' and b'"File"' in content[:200]:
        try:
            data = json.loads(content)
            b64 = (data.get('File') or {}).get('fileContent') or ''
            if not b64:
                return None
            content = base64.b64decode(b64)
            name = (data.get('File') or {}).get('name')
            if name:
                filename = name
        except Exception as e:
            logger.warning(f"unwrap file {file_id}: {e}")
            return None
    if not content or len(content) < 50:
        return None
    return content, filename


def _emails_from_text(text: Optional[str]) -> List[str]:
    from utils.candidate_name_extraction import coalesce_candidate_email
    if not text:
        return []
    found = []
    for m in re.finditer(
        r'([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
        text,
    ):
        cleaned = coalesce_candidate_email(m.group(1))
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def _parse_resume(content: bytes, filename: str) -> Dict[str, Any]:
    from resume_parser import ResumeParser
    suffix = '.pdf'
    lower = filename.lower()
    if lower.endswith('.docx'):
        suffix = '.docx'
    elif lower.endswith('.doc'):
        suffix = '.doc'
    path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            path = tmp.name
        parser = ResumeParser()
        result = parser.parse_resume(path, quick_mode=True, skip_cache=True) or {}
        # Fallback: scrape emails from raw/OCR text when structured parse missed them
        if not result.get('email'):
            blob = ' '.join(
                str(result.get(k) or '')
                for k in ('raw_text', 'formatted_html', 'email', 'phone')
            )
            emails = _emails_from_text(blob)
            if emails:
                result['email'] = emails[0]
        return result
    except Exception as e:
        logger.warning(f"resume parse failed ({filename}): {e}")
        return {}
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def _split_name(full: Optional[str], resume: Dict) -> Tuple[str, str]:
    first = (resume.get('first_name') or '').strip()
    last = (resume.get('last_name') or '').strip()
    if first or last:
        return first, last
    if full and full.strip() and full.strip().lower() != 'none none':
        parts = full.strip().split()
        if len(parts) == 1:
            return parts[0], ''
        return parts[0], ' '.join(parts[1:])
    return '', ''


def _job_id_from_pe(pe) -> Optional[int]:
    if getattr(pe, 'bullhorn_job_id', None):
        return int(pe.bullhorn_job_id)
    from email_inbound_service.extraction_mixin import ExtractionMixin
    class _E(ExtractionMixin):
        def __init__(self):
            self.logger = logger
    return _E().extract_bullhorn_job_id(pe.subject or '', '')


def _reset_pe_for_revet(db, pe) -> None:
    from models import (
        CandidateVettingLog, CandidateJobMatch, EmbeddingFilterLog, EscalationLog,
    )
    logs = CandidateVettingLog.query.filter(
        CandidateVettingLog.parsed_email_id == pe.id
    ).all()
    # Also clear any logs wrongly tied to junk candidate for this application window
    log_ids = [vl.id for vl in logs]
    if log_ids:
        EmbeddingFilterLog.query.filter(
            EmbeddingFilterLog.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        EscalationLog.query.filter(
            EscalationLog.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        CandidateJobMatch.query.filter(
            CandidateJobMatch.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        CandidateVettingLog.query.filter(
            CandidateVettingLog.id.in_(log_ids)
        ).delete(synchronize_session=False)
    pe.vetted_at = None
    if pe.status == 'duplicate':
        pe.status = 'completed'


def repair_one(pe, bh, inbound, dry_run: bool) -> Dict[str, Any]:
    from utils.candidate_name_extraction import coalesce_candidate_email, is_valid_name

    result = {
        'pe_id': pe.id,
        'name': pe.candidate_name,
        'status': 'pending',
        'old_sub': pe.bullhorn_submission_id,
        'job_id': None,
        'new_candidate_id': None,
        'new_submission_id': None,
        'email': None,
        'phone': None,
        'error': None,
    }

    name = (pe.candidate_name or '').strip()
    if not name or name.lower() in ('none none', 'none'):
        result['status'] = 'skipped_noise'
        result['error'] = 'non-candidate / noise row'
        return result

    job_id = _job_id_from_pe(pe)
    result['job_id'] = job_id

    if not pe.resume_file_id:
        # Duplicate sibling without its own upload — cannot split without a résumé
        result['status'] = 'skipped_no_resume'
        result['error'] = 'no resume_file_id on ParsedEmail'
        return result

    downloaded = _download_resume(bh, JUNK_CANDIDATE_ID, int(pe.resume_file_id))
    if not downloaded:
        result['status'] = 'failed_download'
        result['error'] = f'could not download file {pe.resume_file_id}'
        return result
    content, filename = downloaded
    resume = _parse_resume(content, filename)

    first, last = _split_name(name, resume)
    email = coalesce_candidate_email(resume.get('email'), pe.candidate_email)
    phone = resume.get('phone') or None
    # Drop contaminated junk phone if résumé didn't supply a real one
    email_bad, phone_bad = _is_junk_contact(email, phone)
    if email_bad:
        email = coalesce_candidate_email(resume.get('email'))
        email_bad, _ = _is_junk_contact(email, None)
    if phone_bad:
        phone = resume.get('phone') if resume.get('phone') and not _is_junk_contact(None, resume.get('phone'))[1] else None
        # If PE phone looked real and wasn't the junk number, allow it
        pe_phone = getattr(pe, 'candidate_phone', None)
        if not phone and pe_phone and not _is_junk_contact(None, pe_phone)[1]:
            phone = pe_phone

    result['email'] = email
    result['phone'] = phone
    result['name'] = f'{first} {last}'.strip() or name

    if not is_valid_name(first, last) and not (first or last):
        # fall back to PE name parts
        parts = name.split()
        first = parts[0] if parts else 'Unknown'
        last = ' '.join(parts[1:]) if len(parts) > 1 else 'Applicant'

    if not email and not phone:
        # Last resort: allow PE phone even if résumé OCR failed, unless it's the
        # known junk-candidate contamination number.
        pe_phone = getattr(pe, 'candidate_phone', None)
        if pe_phone and not _is_junk_contact(None, pe_phone)[1]:
            phone = pe_phone
            result['phone'] = phone
        else:
            result['status'] = 'skipped_no_contact'
            result['error'] = 'résumé had no usable email or phone after filters'
            return result

    if not email and not phone:
        result['status'] = 'skipped_no_contact'
        result['error'] = 'résumé had no usable email or phone after filters'
        return result

    if dry_run:
        result['status'] = 'dry_run_ok'
        return result

    # Dedupe against REAL contact — never reuse junk 4380273
    dup_id, conf = inbound.find_duplicate_candidate(email or '', phone or '', first, last, bh)
    if dup_id == JUNK_CANDIDATE_ID:
        dup_id, conf = None, 0.0

    if dup_id and conf >= 0.80:
        new_id = dup_id
        # Refresh contact fields if blank on existing
        updates = {}
        existing = bh.get_candidate(new_id) or {}
        if email and not (existing.get('email') or '').strip():
            updates['email'] = email
        if phone and not (existing.get('phone') or existing.get('mobile') or '').strip():
            updates['phone'] = phone
        if updates:
            bh.update_candidate(new_id, updates)
        result['status'] = 'reused_existing'
    else:
        payload = {
            'firstName': first or 'Unknown',
            'lastName': last or 'Applicant',
            'name': f'{first} {last}'.strip(),
            'status': 'Online Applicant',
            'source': SOURCE,
            'owner': {'id': 66},  # Myticas Parser — same as inbound
        }
        if email:
            payload['email'] = email
        if phone:
            payload['phone'] = phone
        new_id = bh.create_candidate(payload)
        if not new_id:
            result['status'] = 'failed_create'
            result['error'] = 'create_candidate returned None'
            return result
        result['status'] = 'created'

    result['new_candidate_id'] = new_id

    new_file_id = bh.upload_candidate_file(new_id, content, filename, file_type='Resume')
    result['new_resume_file_id'] = new_file_id

    new_sub = None
    if job_id:
        new_sub = bh.create_job_submission(new_id, job_id, source=SOURCE)
        result['new_submission_id'] = new_sub
        if not new_sub:
            result['error'] = (result.get('error') or '') + ' submission_create_failed'
    else:
        result['error'] = (result.get('error') or '') + ' no_job_id_in_subject'

    # Soft-delete mis-attributed submission on junk record
    old_sub = pe.bullhorn_submission_id
    if old_sub and int(old_sub) != KEEP_SUBMISSION_ID:
        try:
            bh.delete_entity('JobSubmission', int(old_sub), soft_delete=True)
            result['old_sub_deleted'] = True
        except Exception as e:
            result['old_sub_deleted'] = False
            result['error'] = (result.get('error') or '') + f' old_sub_delete:{e}'

    # Point ParsedEmail at the real candidate
    from extensions import db
    pe.bullhorn_candidate_id = new_id
    pe.bullhorn_submission_id = new_sub
    if new_file_id:
        pe.resume_file_id = new_file_id
    pe.resume_filename = filename
    pe.candidate_email = email
    pe.candidate_phone = phone
    pe.candidate_name = f'{first} {last}'.strip() or name
    pe.is_duplicate_candidate = False
    pe.processing_notes = (
        f"Repaired from Zip collapse on {JUNK_CANDIDATE_ID} → Candidate {new_id}"
        f"{f', Submission {new_sub}' if new_sub else ''} "
        f"at {datetime.utcnow().isoformat()}Z"
    )
    if job_id:
        pe.bullhorn_job_id = job_id
    _reset_pe_for_revet(db, pe)
    db.session.commit()

    return result


def soft_delete_remaining_junk_subs(bh, dry_run: bool) -> List[int]:
    import requests
    deleted = []
    start = 0
    while True:
        r = requests.get(
            f'{bh.base_url}query/JobSubmission',
            params={
                'where': f'candidate.id={JUNK_CANDIDATE_ID} AND isDeleted=false',
                'fields': 'id,jobOrder(id)',
                'count': 50,
                'start': start,
                'BhRestToken': bh.rest_token,
            },
            timeout=30,
        )
        rows = (r.json() or {}).get('data') or []
        if not rows:
            break
        for s in rows:
            sid = int(s['id'])
            if sid == KEEP_SUBMISSION_ID:
                continue
            if dry_run:
                deleted.append(sid)
                continue
            if bh.delete_entity('JobSubmission', sid, soft_delete=True):
                deleted.append(sid)
        if len(rows) < 50:
            break
        start += 50
    return deleted


def neutralize_junk_candidate(bh, dry_run: bool) -> bool:
    """Keep 4380273 (has legitimate historical submission) but strip apply@."""
    payload = {
        # Sentinel that will never match a real applicant or our intake mailbox
        'email': f'do-not-use-collapsed-{JUNK_CANDIDATE_ID}@invalid.jobpulse.local',
        'email2': '',
        'email3': '',
        # Clear contaminated phone that was matching some PE rows
        'phone': '',
        'mobile': '',
        'status': 'Archive',
    }
    if dry_run:
        logger.info(f'DRY_RUN would neutralize Candidate {JUNK_CANDIDATE_ID}: {payload}')
        return True
    ok = bh.update_candidate(JUNK_CANDIDATE_ID, payload)
    return bool(ok)


def main() -> int:
    dry_run = str(os.environ.get('DRY_RUN', '0')).lower() in ('1', 'true', 'yes')
    logger.info(f'=== Zip collapse repair starting (dry_run={dry_run}) ===')

    from app import app
    with app.app_context():
        from extensions import db
        from models import ParsedEmail
        from utils.bullhorn_helpers import get_bullhorn_service
        from email_inbound_service import EmailInboundService

        bh = get_bullhorn_service()
        bh.authenticate()
        inbound = EmailInboundService()

        pes = (
            ParsedEmail.query
            .filter(ParsedEmail.bullhorn_candidate_id == JUNK_CANDIDATE_ID)
            .order_by(ParsedEmail.id.asc())
            .all()
        )
        logger.info(f'Found {len(pes)} ParsedEmail rows on {JUNK_CANDIDATE_ID}')

        summary = {
            'created': 0,
            'reused_existing': 0,
            'dry_run_ok': 0,
            'skipped_noise': 0,
            'skipped_no_resume': 0,
            'skipped_no_contact': 0,
            'failed': 0,
            'results': [],
        }

        for pe in pes:
            try:
                r = repair_one(pe, bh, inbound, dry_run=dry_run)
            except Exception as e:
                logger.exception(f'PE {pe.id} crashed')
                r = {
                    'pe_id': pe.id,
                    'name': pe.candidate_name,
                    'status': 'failed_exception',
                    'error': str(e)[:300],
                }
                db.session.rollback()
            summary['results'].append(r)
            st = r.get('status') or ''
            if st in summary:
                summary[st] += 1
            elif st.startswith('failed'):
                summary['failed'] += 1
            logger.info(
                f"PE {r.get('pe_id')}: {st} name={r.get('name')!r} "
                f"email={r.get('email')!r} phone={r.get('phone')!r} "
                f"job={r.get('job_id')} → cand={r.get('new_candidate_id')} "
                f"sub={r.get('new_submission_id')} err={r.get('error')}"
            )

        deleted_subs = soft_delete_remaining_junk_subs(bh, dry_run=dry_run)
        summary['junk_subs_soft_deleted'] = deleted_subs
        logger.info(f'Junk submissions soft-deleted: {len(deleted_subs)} → {deleted_subs[:20]}...')

        neutralized = neutralize_junk_candidate(bh, dry_run=dry_run)
        summary['junk_candidate_neutralized'] = neutralized

        if not dry_run:
            # Clear any remaining vetting logs that were attributed to the junk id
            # for repaired PE rows (already cleared per-PE). Enqueue screening.
            try:
                from utils.screening_dispatch import enqueue_vetting_now
                enq = enqueue_vetting_now(reason='zip_collapse_repair')
                summary['vetting_enqueued'] = enq
                logger.info(f'Vetting enqueue: {enq}')
            except Exception as e:
                summary['vetting_enqueued'] = {'error': str(e)}
                logger.warning(f'Could not enqueue vetting (PEs have vetted_at=NULL): {e}')

        print('=== SUMMARY JSON ===')
        print(json.dumps({
            k: v for k, v in summary.items() if k != 'results'
        }, indent=2, default=str))
        print('=== PER-ROW ===')
        for r in summary['results']:
            print(json.dumps(r, default=str))

        remaining = ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id == JUNK_CANDIDATE_ID
        ).count()
        print(f'REMAINING_ON_JUNK={remaining}')
        return 0 if summary['failed'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
