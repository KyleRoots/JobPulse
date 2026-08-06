#!/usr/bin/env python3
"""Second-pass Zip collapse repair for rows still on Candidate 4380273."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
for name in ('extensions', 'seeding', 'apscheduler', 'bullhorn_service.jobs', 'httpx'):
    logging.getLogger(name).setLevel(logging.WARNING)

logger = logging.getLogger('repair_zip_pass2')

JUNK = 4380273
KEEP_SUB = 620809
SOURCE = 'ZipRecruiter Job Board'


def junk_phone(p: str | None) -> bool:
    d = re.sub(r'\D', '', p or '')
    return d in ('', '4044443953')


def main() -> int:
    from app import app
    with app.app_context():
        from extensions import db
        from models import (
            ParsedEmail, CandidateVettingLog, CandidateJobMatch,
            EmbeddingFilterLog, EscalationLog,
        )
        from utils.bullhorn_helpers import get_bullhorn_service
        from utils.candidate_name_extraction import coalesce_candidate_email
        from email_inbound_service import EmailInboundService
        from email_inbound_service.extraction_mixin import ExtractionMixin
        import requests

        bh = get_bullhorn_service()
        bh.authenticate()
        inbound = EmailInboundService()

        class _E(ExtractionMixin):
            def __init__(self):
                self.logger = logger

        extractor = _E()

        def download(fid):
            r = requests.get(
                f'{bh.base_url}file/Candidate/{JUNK}/{fid}',
                params={'BhRestToken': bh.rest_token},
                timeout=60,
            )
            if r.status_code != 200:
                return None
            c = r.content
            fn = f'resume_{fid}.pdf'
            if c and c.lstrip()[:1] == b'{' and b'"File"' in c[:200]:
                data = json.loads(c)
                b64 = (data.get('File') or {}).get('fileContent') or ''
                if not b64:
                    return None
                c = base64.b64decode(b64)
                fn = (data.get('File') or {}).get('name') or fn
            return c, fn

        def deep_parse(content, filename):
            from resume_parser import ResumeParser
            from vetting.resume_utils import extract_resume_text
            path = None
            try:
                suf = '.pdf'
                if filename.lower().endswith('.docx'):
                    suf = '.docx'
                with tempfile.NamedTemporaryFile(delete=False, suffix=suf) as t:
                    t.write(content)
                    path = t.name
                res = ResumeParser().parse_resume(path, quick_mode=False, skip_cache=True) or {}
                if not res.get('email') or not res.get('phone'):
                    raw = extract_resume_text(content, filename) or res.get('raw_text') or ''
                    res['raw_text'] = raw
                    if not res.get('email'):
                        for m in re.finditer(
                            r'([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
                            raw,
                        ):
                            e = coalesce_candidate_email(m.group(1))
                            if e:
                                res['email'] = e
                                break
                    if not res.get('phone'):
                        ph = re.search(
                            r'(\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4})',
                            raw or '',
                        )
                        if ph:
                            res['phone'] = ph.group(1)
                return res
            finally:
                if path and os.path.exists(path):
                    os.unlink(path)

        def reset_pe(pe):
            logs = CandidateVettingLog.query.filter_by(parsed_email_id=pe.id).all()
            ids = [l.id for l in logs]
            if ids:
                EmbeddingFilterLog.query.filter(
                    EmbeddingFilterLog.vetting_log_id.in_(ids)
                ).delete(synchronize_session=False)
                EscalationLog.query.filter(
                    EscalationLog.vetting_log_id.in_(ids)
                ).delete(synchronize_session=False)
                CandidateJobMatch.query.filter(
                    CandidateJobMatch.vetting_log_id.in_(ids)
                ).delete(synchronize_session=False)
                CandidateVettingLog.query.filter(
                    CandidateVettingLog.id.in_(ids)
                ).delete(synchronize_session=False)
            pe.vetted_at = None
            if pe.status == 'duplicate':
                pe.status = 'completed'

        pes = ParsedEmail.query.filter_by(bullhorn_candidate_id=JUNK).order_by(ParsedEmail.id).all()
        logger.info(f'REMAINING {len(pes)}')

        for pe in pes:
            name = (pe.candidate_name or '').strip()
            logger.info(
                f'--- {pe.id} {name!r} resume={pe.resume_file_id} '
                f'phone={pe.candidate_phone} sub={pe.bullhorn_submission_id}'
            )
            if not name or name.lower() in ('none none', 'none'):
                pe.processing_notes = f'Detached noise row from Zip collapse {JUNK}'
                pe.bullhorn_candidate_id = None
                pe.status = 'ignored'
                db.session.commit()
                logger.info('  detached noise')
                continue

            parts = name.split()
            first, last = (parts[0], ' '.join(parts[1:])) if parts else ('Unknown', 'Applicant')
            job_id = pe.bullhorn_job_id or extractor.extract_bullhorn_job_id(pe.subject or '', '')
            email = None
            phone = pe.candidate_phone if not junk_phone(pe.candidate_phone) else None
            content = filename = None
            resume = {}

            if pe.resume_file_id:
                dl = download(int(pe.resume_file_id))
                if dl:
                    content, filename = dl
                    resume = deep_parse(content, filename)
                    email = coalesce_candidate_email(resume.get('email'))
                    if resume.get('phone') and not junk_phone(resume.get('phone')):
                        phone = resume.get('phone')
                    if resume.get('first_name'):
                        first = resume['first_name']
                    if resume.get('last_name'):
                        last = resume['last_name']

            if not email and not phone:
                pe.processing_notes = (
                    f'Zip collapse repair incomplete: no usable contact. '
                    f'Still on junk {JUNK}. Manual intake needed. '
                    f'{datetime.utcnow().isoformat()}Z'
                )
                db.session.commit()
                logger.info('  still no contact — flagged for manual')
                continue

            dup_id, conf = inbound.find_duplicate_candidate(
                email or '', phone or '', first, last, bh
            )
            if dup_id == JUNK:
                dup_id, conf = None, 0.0
            if dup_id and conf >= 0.80:
                new_id = dup_id
                status = 'reused'
            else:
                payload = {
                    'firstName': first,
                    'lastName': last or 'Applicant',
                    'name': f'{first} {last}'.strip(),
                    'status': 'Online Applicant',
                    'source': SOURCE,
                    'owner': {'id': 66},
                }
                if email:
                    payload['email'] = email
                if phone:
                    payload['phone'] = phone
                new_id = bh.create_candidate(payload)
                status = 'created'
                if not new_id:
                    logger.error('  create failed')
                    continue

            new_file = None
            if content and filename:
                new_file = bh.upload_candidate_file(
                    new_id, content, filename, file_type='Resume'
                )
            new_sub = (
                bh.create_job_submission(new_id, int(job_id), source=SOURCE)
                if job_id else None
            )
            if pe.bullhorn_submission_id and int(pe.bullhorn_submission_id) != KEEP_SUB:
                bh.delete_entity(
                    'JobSubmission', int(pe.bullhorn_submission_id), soft_delete=True
                )

            pe.bullhorn_candidate_id = new_id
            pe.bullhorn_submission_id = new_sub
            if new_file:
                pe.resume_file_id = new_file
            pe.candidate_email = email
            pe.candidate_phone = phone
            pe.candidate_name = f'{first} {last}'.strip()
            pe.is_duplicate_candidate = False
            if job_id:
                pe.bullhorn_job_id = job_id
            pe.processing_notes = (
                f'Second-pass Zip collapse repair → {new_id} ({status}) '
                f'{datetime.utcnow().isoformat()}Z'
            )
            reset_pe(pe)
            db.session.commit()
            logger.info(
                f'  {status} → cand={new_id} sub={new_sub} email={email} phone={phone}'
            )

        cand = bh.get_candidate(JUNK) or {}
        logger.info(
            f"JUNK email={cand.get('email')} status={cand.get('status')} "
            f"phone={cand.get('phone')}"
        )
        # Ensure neutralized
        if (cand.get('email') or '').lower() == 'apply@myticas.com':
            bh.update_candidate(JUNK, {
                'email': f'do-not-use-collapsed-{JUNK}@invalid.jobpulse.local',
                'phone': '',
                'mobile': '',
                'status': 'Archive',
            })
            logger.info('  re-neutralized junk candidate')

        r = requests.get(
            f'{bh.base_url}query/JobSubmission',
            params={
                'where': f'candidate.id={JUNK} AND isDeleted=false',
                'fields': 'id',
                'count': 100,
                'BhRestToken': bh.rest_token,
            },
            timeout=30,
        )
        for s in (r.json().get('data') or []):
            sid = int(s['id'])
            if sid == KEEP_SUB:
                continue
            bh.delete_entity('JobSubmission', sid, soft_delete=True)
            logger.info(f'deleted leftover sub {sid}')

        left = ParsedEmail.query.filter_by(bullhorn_candidate_id=JUNK).all()
        print(f'REMAINING_ON_JUNK={len(left)}')
        for pe in left:
            print(f' leftover pe={pe.id} name={pe.candidate_name!r} notes={(pe.processing_notes or "")[:120]}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
