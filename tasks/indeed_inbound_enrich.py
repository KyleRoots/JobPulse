"""Fill gaps on native Indeed Apply candidates.

Indeed creates the candidate inside Bullhorn, so Scout's email ingest never
runs. This follow-up, on the same 5-minute Indeed remap cycle:

* Qualified only: copy the applied job's Internal Department onto the
  candidate when it is missing (same field pair as mailbox / portal ingest).
* Qualified only: write one Application Received note (job, company,
  recruiter, time, source, then the AI résumé summary). Job, company, and
  recruiter names link to their Bullhorn records.
* Any tenant: if Qualified did not write that combined note, write an AI
  Resume Summary from the resume file Bullhorn already stored.

Existing department values and completed snapshot notes are left alone.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

import requests as _requests

logger = logging.getLogger(__name__)

SUMMARY_ACTION = 'AI Resume Summary'
MAX_SCAN = 300
MAX_DEPARTMENT_UPDATES = 40
MAX_SUMMARIES = 8
MAX_SNAPSHOTS = 40


def department_update(current: Any, job_department: Optional[str]) -> Optional[str]:
    """Return the department to write, or None when no change is needed."""
    incoming = (job_department or '').strip()
    if not incoming:
        return None
    existing = str(current or '').strip()
    if existing == incoming:
        return None
    if existing:
        return None
    return incoming


def latest_job_id(submissions: List[Dict[str, Any]]) -> Optional[int]:
    for row in submissions or []:
        job = row.get('jobOrder') or {}
        jid = job.get('id') if isinstance(job, dict) else None
        if jid:
            try:
                return int(jid)
            except (TypeError, ValueError):
                continue
    return None


def summary_note_text(resume_data: Dict[str, Any]) -> str:
    """Same résumé-summary body used inside the Qualified application note."""
    from inbound_application_note import resume_summary_body

    return resume_summary_body(resume_data)


def _has_summary_note(notes: List[Dict[str, Any]]) -> bool:
    return any((n.get('action') or '') == SUMMARY_ACTION for n in notes or [])


def _resume_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in files or []:
        name = (item.get('name') or '').lower()
        kind = (item.get('type') or '').lower()
        if kind == 'resume' or name.endswith(('.pdf', '.doc', '.docx')):
            return item
    return None


def _extract_resume_text(base_url: str, headers: Dict[str, str], candidate_id: int, file_info: Dict[str, Any]) -> str:
    import base64

    file_id = file_info.get('id')
    filename = file_info.get('name') or 'resume.pdf'
    if not file_id:
        return ''
    response = _requests.get(
        f'{base_url}file/Candidate/{candidate_id}/{file_id}',
        headers=headers,
        timeout=30,
    )
    if response.status_code != 200:
        return ''
    content = ((response.json() or {}).get('File') or {}).get('fileContent') or ''
    if not content:
        return ''
    raw = base64.b64decode(content)
    suffix = '.pdf'
    lower = filename.lower()
    if lower.endswith('.docx'):
        suffix = '.docx'
    elif lower.endswith('.doc'):
        suffix = '.doc'
    path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            path = tmp.name
        from resume_parser import ResumeParser
        parsed = ResumeParser().parse_resume(path, quick_mode=True, skip_cache=True)
        return (parsed.get('raw_text') or '').strip()
    except Exception as exc:
        logger.warning('indeed enrich: resume text failed candidate %s: %s', candidate_id, exc)
        return ''
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def enrich_indeed_job_board_candidates() -> Dict[str, Any]:
    """Backfill Internal Department and AI Resume Summary for Indeed Job Board."""
    summary = {
        'scanned': 0,
        'department_updated': 0,
        'summaries_created': 0,
        'snapshots_created': 0,
        'skipped': 0,
        'failed': 0,
    }
    from bullhorn_service import BullhornService
    from utils.job_internal_department import (
        fetch_job_internal_department,
        should_mirror_job_internal_department,
    )

    mirror_dept = should_mirror_job_internal_department()
    bh = BullhornService()
    if not bh.authenticate():
        logger.warning('indeed enrich: Bullhorn auth failed')
        return summary

    headers = {
        'BhRestToken': bh.rest_token,
        'Accept': 'application/json',
    }
    parser = None
    dept_budget = MAX_DEPARTMENT_UPDATES
    summary_budget = MAX_SUMMARIES
    snapshot_budget = MAX_SNAPSHOTS
    start = 0

    while start < MAX_SCAN and (
        dept_budget > 0 or summary_budget > 0 or snapshot_budget > 0
    ):
        response = _requests.get(
            f'{bh.base_url}search/Candidate',
            headers=headers,
            params={
                'query': 'source:"Indeed Job Board" AND isDeleted:false',
                'fields': 'id,firstName,lastName,customText3,source',
                'count': 50,
                'start': start,
                'sort': '-dateAdded',
            },
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning('indeed enrich: search HTTP %s', response.status_code)
            break
        rows = (response.json() or {}).get('data') or []
        if not rows:
            break
        for candidate in rows:
            if dept_budget <= 0 and summary_budget <= 0 and snapshot_budget <= 0:
                break
            cid = candidate.get('id')
            if not cid:
                continue
            summary['scanned'] += 1
            try:
                submissions = None

                def _submissions():
                    nonlocal submissions
                    if submissions is None:
                        submissions = bh.get_candidate_submissions(int(cid), count=5)
                    return submissions

                dept_written = False
                if mirror_dept and dept_budget > 0:
                    job_id = latest_job_id(_submissions())
                    incoming = fetch_job_internal_department(bh, job_id) if job_id else None
                    value = department_update(candidate.get('customText3'), incoming)
                    if value:
                        upd = _requests.post(
                            f'{bh.base_url}entity/Candidate/{cid}',
                            headers={**headers, 'Content-Type': 'application/json'},
                            json={'customText3': value},
                            timeout=20,
                        )
                        if upd.status_code in (200, 201):
                            summary['department_updated'] += 1
                            dept_budget -= 1
                            dept_written = True
                            logger.info(
                                'indeed enrich: candidate %s Internal Department %r from job %s',
                                cid, value, job_id,
                            )
                        else:
                            summary['failed'] += 1

                snapshot_written = False
                from feeds.feed_config import is_qualified_tenant
                qualified = is_qualified_tenant()
                job_id = latest_job_id(_submissions()) if (qualified or (mirror_dept and dept_budget > 0)) else None
                if snapshot_budget > 0 and qualified:
                    from inbound_application_note import (
                        SUMMARY_HEADING,
                        resume_summary_body,
                        write_application_received_note,
                    )

                    rows_sub = _submissions() or []
                    applied_at = rows_sub[0].get('dateAdded') if rows_sub else None
                    if job_id:
                        summary_body = ''
                        prior = bh.get_candidate_notes(
                            int(cid),
                            action_filter=[SUMMARY_ACTION, 'Application Received'],
                            count=20,
                        )
                        already_combined = any(
                            SUMMARY_HEADING in (n.get('comments') or '')
                            and (n.get('action') or '') == 'Application Received'
                            for n in prior or []
                        )
                        has_ai_note = any(
                            (n.get('action') or '') == SUMMARY_ACTION
                            and SUMMARY_HEADING in (n.get('comments') or '')
                            for n in prior or []
                        )
                        if (
                            not already_combined
                            and not has_ai_note
                            and summary_budget > 0
                        ):
                            files_resp = _requests.get(
                                f'{bh.base_url}entity/Candidate/{cid}/fileAttachments',
                                headers=headers,
                                params={
                                    'fields': 'id,name,type',
                                    'BhRestToken': bh.rest_token,
                                },
                                timeout=20,
                            )
                            files = []
                            if files_resp.status_code == 200:
                                files = (files_resp.json() or {}).get('data') or []
                            resume = _resume_file(files)
                            if resume:
                                text = _extract_resume_text(
                                    bh.base_url, headers, int(cid), resume
                                )
                                if parser is None:
                                    from email_inbound_service import EmailInboundService
                                    parser = EmailInboundService()
                                parsed = parser.parse_resume_with_ai(text) if text else {}
                                summary_body = resume_summary_body(parsed)
                                if summary_body:
                                    summary_budget -= 1
                        snap_id = write_application_received_note(
                            bh,
                            int(cid),
                            job_id,
                            source=candidate.get('source') or 'Indeed Job Board',
                            applied_at=applied_at,
                            summary_text=summary_body,
                        )
                        if snap_id:
                            summary['snapshots_created'] += 1
                            snapshot_budget -= 1
                            snapshot_written = True
                            logger.info(
                                'indeed enrich: Application Received note on candidate %s job %s',
                                cid, job_id,
                            )

                note_written = False
                if summary_budget > 0 and not (qualified and job_id):
                    notes = bh.get_candidate_notes(int(cid), action_filter=[SUMMARY_ACTION], count=5)
                    if not _has_summary_note(notes):
                        files_resp = _requests.get(
                            f'{bh.base_url}entity/Candidate/{cid}/fileAttachments',
                            headers=headers,
                            params={'fields': 'id,name,type', 'BhRestToken': bh.rest_token},
                            timeout=20,
                        )
                        files = []
                        if files_resp.status_code == 200:
                            files = (files_resp.json() or {}).get('data') or []
                        resume = _resume_file(files)
                        if resume:
                            text = _extract_resume_text(bh.base_url, headers, int(cid), resume)
                            if parser is None:
                                from email_inbound_service import EmailInboundService
                                parser = EmailInboundService()
                            parsed = parser.parse_resume_with_ai(text) if text else {}
                            note = summary_note_text(parsed)
                            if note and bh.create_candidate_note(int(cid), note, SUMMARY_ACTION):
                                summary['summaries_created'] += 1
                                summary_budget -= 1
                                note_written = True
                                logger.info('indeed enrich: AI Resume Summary on candidate %s', cid)
                if not dept_written and not note_written and not snapshot_written:
                    summary['skipped'] += 1
            except Exception as exc:
                summary['failed'] += 1
                logger.warning('indeed enrich: candidate %s failed: %s', cid, exc)
        if len(rows) < 50:
            break
        start += len(rows)

    logger.info(
        'indeed enrich: scanned=%s department=%s summaries=%s snapshots=%s skipped=%s failed=%s',
        summary['scanned'],
        summary['department_updated'],
        summary['summaries_created'],
        summary['snapshots_created'],
        summary['skipped'],
        summary['failed'],
    )
    return summary
