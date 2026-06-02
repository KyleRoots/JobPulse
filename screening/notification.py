from __future__ import annotations
"""
Notification Service - Recruiter email notifications for qualified candidates.

Contains:
- send_recruiter_notifications: Sends consolidated email with all recruiters CC'd
- _send_recruiter_email: Builds and sends the HTML notification email
- _build_recruiter_subject: Compose job-aware subject line (Option A format)
- _fetch_resume_attachment: Best-effort resume fetch for inbox keyword search
"""

import logging
import re
logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional
from app import db
from models import CandidateJobMatch, CandidateVettingLog, VettingConfig
from vetting.name_utils import parse_names, parse_emails
from screening.location_review import is_location_review_match, resolve_match_threshold

# Window for "this recruiter already got an email for this (candidate, job)
# pair" dedupe. Set wide enough to absorb the auditor's same-day re-vet flow
# but short enough that genuine re-applications a few weeks later still
# trigger a fresh send. Task #95 — see RecruiterNotificationLedger docstring.
_RECRUITER_NOTIFICATION_DEDUPE_WINDOW_HOURS = 24

# Resume attachment cap — SendGrid hard limit is ~30MB on the full payload;
# 10MB keeps headroom for the HTML body, base64 overhead (~33%), and matches
# typical Outlook/Gmail comfort zones for forwarded attachments.
_RESUME_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024

# File extension → MIME type for resume attachments. Default keeps SendGrid
# happy with arbitrary bytes; Outlook/Gmail still render based on filename.
_RESUME_CONTENT_TYPE_MAP = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.rtf': 'application/rtf',
    '.txt': 'text/plain',
    '.odt': 'application/vnd.oasis.opendocument.text',
}


def _build_recruiter_subject(candidate_name: str, matches: List['CandidateJobMatch']) -> str:
    """
    Compose a job-aware subject line so recruiters can triage from the inbox
    without opening every Scout email.

    Option A format (per internal user request, May 2026):
      - 1 match    → "Scout: {Name} — {Job Title} (Job #{ID})"
      - N matches  → "Scout: {Name} — {Top Job Title} (Job #{ID}) +{N-1} more"
      - Empty/edge → falls back to the legacy "Qualified Candidate Alert"
        format so we never send a malformed subject.

    Top match is the highest-scored match (matches with no `match_score`
    fall to the bottom; ties keep input order — stable sort).
    """
    safe_name = (candidate_name or 'Candidate').strip() or 'Candidate'
    if not matches:
        return f"🎯 Qualified Candidate Alert: {safe_name}"

    sorted_matches = sorted(
        matches,
        key=lambda m: (m.match_score or 0),
        reverse=True,
    )
    top = sorted_matches[0]
    top_title = (top.job_title or 'Position').strip() or 'Position'
    top_job_id = top.bullhorn_job_id

    if top_job_id:
        head = f"Scout: {safe_name} — {top_title} (Job #{top_job_id})"
    else:
        head = f"Scout: {safe_name} — {top_title}"

    extra = len(sorted_matches) - 1
    if extra > 0:
        return f"{head} +{extra} more"
    return head


def _resume_content_type(filename: Optional[str]) -> str:
    """Best-effort MIME inference from filename extension."""
    if not filename:
        return 'application/octet-stream'
    lower = filename.lower()
    for ext, ctype in _RESUME_CONTENT_TYPE_MAP.items():
        if lower.endswith(ext):
            return ctype
    return 'application/octet-stream'


def _safe_resume_filename(candidate_name: str, original_filename: Optional[str]) -> str:
    """
    Build an inbox-friendly resume filename: `{Candidate_Name}_Resume.{ext}`.

    Preserves the original extension so the recipient's mail client picks the
    right icon and the OS opens it in the right app. Sanitizes the name to
    a conservative ASCII subset so SendGrid/MIME don't choke on non-ASCII.
    """
    name = (candidate_name or 'Candidate').strip() or 'Candidate'
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('_') or 'Candidate'

    ext = ''
    if original_filename:
        match = re.search(r'(\.[A-Za-z0-9]{2,5})$', original_filename)
        if match:
            ext = match.group(1).lower()
    if not ext:
        ext = '.pdf'  # Sensible default — most Bullhorn resumes are PDFs.
    return f"{safe_name}_Resume{ext}"


def _ledger_recently_sent_pairs(
    candidate_id: int,
    job_ids: Iterable[int],
    notification_type: str,
    lookback_hours: int = _RECRUITER_NOTIFICATION_DEDUPE_WINDOW_HOURS,
) -> Dict[int, datetime]:
    """Return a mapping of bullhorn_job_id → most-recent ``sent_at`` for
    pairs that already had a recruiter email within the lookback window.

    Used by every recruiter-notification entry point to dedupe across the
    auditor cascade (which wipes ``CandidateJobMatch.notification_sent``).
    Fail-open: any DB error returns an empty dict so a transient lookup
    failure never blocks a genuine first-time email.
    """
    from models import RecruiterNotificationLedger

    job_id_list = [int(jid) for jid in job_ids if jid is not None]
    if not job_id_list or candidate_id is None:
        return {}

    try:
        cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
        rows = RecruiterNotificationLedger.query.filter(
            RecruiterNotificationLedger.bullhorn_candidate_id == int(candidate_id),
            RecruiterNotificationLedger.notification_type == notification_type,
            RecruiterNotificationLedger.bullhorn_job_id.in_(job_id_list),
            RecruiterNotificationLedger.sent_at >= cutoff,
        ).all()
    except Exception as e:
        logger.warning(
            f"⚠️ recruiter notification ledger lookup failed "
            f"(candidate={candidate_id}, type={notification_type}): {e!r} — "
            f"proceeding without dedupe"
        )
        return {}

    result: Dict[int, datetime] = {}
    for r in rows:
        prior = result.get(r.bullhorn_job_id)
        if prior is None or (r.sent_at and r.sent_at > prior):
            result[r.bullhorn_job_id] = r.sent_at
    return result


def _record_ledger_sent(
    candidate_id: int,
    job_ids: Iterable[int],
    notification_type: str,
) -> int:
    """Upsert ledger rows for each (candidate, job, notification_type)
    after a successful recruiter email. Idempotent and race-tolerant —
    a concurrent cycle that already wrote the same row simply causes a
    rollback of the duplicate insert without surfacing an error.

    Returns the number of rows successfully written/updated.
    """
    from models import RecruiterNotificationLedger
    from sqlalchemy.exc import IntegrityError

    if candidate_id is None:
        return 0

    written = 0
    now = datetime.utcnow()
    seen: set = set()
    for jid in job_ids:
        if jid is None or int(jid) in seen:
            continue
        seen.add(int(jid))
        try:
            existing = RecruiterNotificationLedger.query.filter_by(
                bullhorn_candidate_id=int(candidate_id),
                bullhorn_job_id=int(jid),
                notification_type=notification_type,
            ).first()
            if existing is not None:
                existing.sent_at = now
            else:
                db.session.add(RecruiterNotificationLedger(
                    bullhorn_candidate_id=int(candidate_id),
                    bullhorn_job_id=int(jid),
                    notification_type=notification_type,
                    sent_at=now,
                ))
            db.session.commit()
            written += 1
        except IntegrityError:
            # Concurrent cycle wrote the same (candidate, job, type) row
            # between our SELECT and INSERT. Treat as success — the dedupe
            # signal exists either way.
            db.session.rollback()
            written += 1
        except Exception as e:
            db.session.rollback()
            logger.warning(
                f"⚠️ recruiter notification ledger write failed "
                f"(candidate={candidate_id}, job={jid}, type={notification_type}): "
                f"{e!r}"
            )
    return written


def _filter_matches_by_ledger(
    matches: List['CandidateJobMatch'],
    candidate_id: int,
    notification_type: str,
) -> tuple:
    """Split ``matches`` into (matches_to_send, suppressed_matches) using
    the RecruiterNotificationLedger. Suppressed matches are still marked
    ``notification_sent=True`` by the caller so subsequent regular cycles
    keep their existing dedupe behaviour, and a structured suppression
    log marker is emitted for observability.

    Matches without a ``bullhorn_job_id`` cannot be deduped — they pass
    through to the send list (better to risk a duplicate than drop a
    legitimate notification when we can't identify the pair).
    """
    if not matches or candidate_id is None:
        return list(matches), []

    job_ids = [m.bullhorn_job_id for m in matches if m.bullhorn_job_id]
    if not job_ids:
        return list(matches), []

    already = _ledger_recently_sent_pairs(candidate_id, job_ids, notification_type)
    if not already:
        return list(matches), []

    to_send: List = []
    suppressed: List = []
    for m in matches:
        jid = m.bullhorn_job_id
        if jid is not None and int(jid) in already:
            suppressed.append(m)
            prior = already.get(int(jid))
            logger.info(
                f"event=recruiter_email_suppressed_already_sent "
                f"candidate_id={candidate_id} job_id={jid} "
                f"notification_type={notification_type} "
                f"prior_sent_at={prior.isoformat() if prior else 'unknown'}"
            )
        else:
            to_send.append(m)
    return to_send, suppressed


class NotificationMixin:
    """Recruiter email notifications for qualified screening matches."""

    def send_recruiter_notifications(self, vetting_log: CandidateVettingLog) -> int:
        """
        Send ONE email notification with all recruiters CC'd.
        
        TRANSPARENCY MODEL: When a candidate matches multiple positions with different
        recruiters, ALL recruiters are CC'd on the SAME email thread. The primary
        recipient is the recruiter of the job the candidate applied to. This ensures
        complete visibility and enables direct collaboration on the same thread.
        
        Args:
            vetting_log: The vetting log with qualified matches
            
        Returns:
            Number of notifications sent (1 for success, 0 for failure/no matches)
        """
        # SAFETY CHECK: Re-verify vetting is still enabled before sending emails
        # This prevents emails if vetting was disabled mid-cycle
        # Force fresh database read to bypass SQLAlchemy session cache
        db.session.expire_all()
        if not self.is_enabled():
            logger.info(f"📧 Notification blocked - vetting disabled mid-cycle for {vetting_log.candidate_name}")
            return 0
        
        logger.info(f"📧 Notification check for {vetting_log.candidate_name} (ID: {vetting_log.bullhorn_candidate_id})")
        
        if not vetting_log.is_qualified:
            # Try Location Review first — most specific signal (tech-qualified candidate
            # knocked just below threshold by a small location penalty)
            location_sent = self._send_location_review_notification(vetting_log)
            if location_sent:
                return location_sent
            # Fall back to prestige review (Tier-1 firm employer, below threshold)
            prestige_sent = self._send_prestige_review_notification(vetting_log)
            if not prestige_sent:
                logger.info(f"  ⏭️ Skipping - not qualified (is_qualified={vetting_log.is_qualified})")
            return prestige_sent
        
        # Get ALL qualified matches for this candidate
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            is_qualified=True,
            notification_sent=False
        ).all()
        
        if not matches:
            logger.info(f"  ⏭️ Skipping - no unsent qualified matches (all already notified)")
            return 0
        
        logger.info(f"  📨 Found {len(matches)} unsent qualified matches")

        # ── Cross-revet dedupe (Task #95) ─────────────────────────────
        # The Quality Auditor's clear_candidate_vetting_state cascade
        # wipes the notification_sent flag along with the matches, so
        # without a durable signal we'd fire a second email after a
        # re-vet even though the Bullhorn note path correctly skips
        # the duplicate. Filter out (candidate, job) pairs we already
        # emailed for within the dedupe window — still mark the new
        # rows as sent so subsequent regular cycles dedupe normally,
        # and emit a structured suppression marker.
        matches, suppressed_matches = _filter_matches_by_ledger(
            matches, vetting_log.bullhorn_candidate_id, 'qualified',
        )
        if suppressed_matches:
            now = datetime.utcnow()
            for m in suppressed_matches:
                m.notification_sent = True
                m.notification_sent_at = now
            try:
                db.session.commit()
            except Exception as commit_err:
                logger.warning(
                    f"⚠️ failed to mark suppressed matches as sent "
                    f"(candidate={vetting_log.bullhorn_candidate_id}): "
                    f"{commit_err!r}"
                )
                db.session.rollback()

        if not matches:
            logger.info(
                f"  ⏭️ Skipping recruiter email for "
                f"{vetting_log.candidate_name} — every qualified "
                f"(candidate, job) pair was already emailed within the "
                f"last {_RECRUITER_NOTIFICATION_DEDUPE_WINDOW_HOURS}h "
                f"(suppressed={len(suppressed_matches)})"
            )
            return 0

        # ── Recruiter transparency (May 2026) — Applied-job context ──
        # When a candidate qualifies for related jobs but NOT for the role they
        # actually applied to, the recipient recruiter has no way to know that
        # context. Pull the applied-job match (regardless of qualified status)
        # and pass it to the email renderer as a separate "context" block so
        # the recruiter sees "this person applied to Job X, scored Y%, here's
        # why they're being shown to you for Job Z instead." Canonical
        # reproducer: candidate 3808669 (Lei Gao). Fail-soft: any DB error
        # here just suppresses the context block — never blocks the email.
        applied_context_match = None
        try:
            applied_already_in_matches = any(
                getattr(m, 'is_applied_job', False) for m in matches
            )
            if not applied_already_in_matches and getattr(vetting_log, 'applied_job_id', None):
                applied_context_match = CandidateJobMatch.query.filter_by(
                    vetting_log_id=vetting_log.id,
                    is_applied_job=True,
                ).first()
                if applied_context_match:
                    logger.info(
                        f"  📌 Applied-job context: Job #{applied_context_match.bullhorn_job_id} "
                        f"\"{applied_context_match.job_title}\" — "
                        f"{(applied_context_match.match_score or 0):.0f}% (below qualifying threshold)"
                    )
        except Exception as ctx_err:
            logger.warning(
                f"Applied-job context lookup failed (proceeding without): {ctx_err!r}"
            )
            applied_context_match = None
        
        # Determine primary recruiter (from applied job) and CC list
        # Note: recruiter_email may now be comma-separated (multiple recruiters per job)
        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []
        
        # First pass: find the applied job recruiter (primary recipient)
        for match in matches:
            if match.is_applied_job and match.recruiter_email:
                emails = parse_emails(match.recruiter_email)
                names = parse_names(match.recruiter_name)
                if emails:
                    primary_recruiter_email = emails[0]  # First recruiter on applied job is primary
                    primary_recruiter_name = names[0] if names else ''
                break
        
        # Second pass: collect all unique recruiter emails from all matches
        # If no applied job recruiter found, first recruiter becomes primary
        seen_emails = set()
        for match in matches:
            emails = parse_emails(match.recruiter_email)
            names = parse_names(match.recruiter_name)
            
            for i, email in enumerate(emails):
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    name = names[i] if i < len(names) else ''
                    
                    if not primary_recruiter_email:
                        # No applied job match - first recruiter becomes primary
                        primary_recruiter_email = email
                        primary_recruiter_name = name
                    elif email != primary_recruiter_email:
                        # Different from primary - add to CC list
                        cc_recruiter_emails.append(email)
        
        # Check email notification kill switch setting
        from models import VettingConfig
        send_to_recruiters = False
        admin_email = ''
        
        send_setting = VettingConfig.query.filter_by(setting_key='send_recruiter_emails').first()
        if send_setting:
            send_to_recruiters = send_setting.setting_value.lower() == 'true'
        
        admin_setting = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
        if admin_setting and admin_setting.setting_value:
            admin_email = admin_setting.setting_value
        
        # If kill switch is OFF, send only to admin email
        if not send_to_recruiters:
            if not admin_email:
                logger.warning(f"❌ Recruiter emails disabled but no admin email configured - cannot send notification for {vetting_log.candidate_name}")
                return 0
            
            logger.info(f"  🔒 Recruiter emails DISABLED - sending to admin only: {admin_email}")
            primary_recruiter_email = admin_email
            primary_recruiter_name = 'Admin'
            cc_recruiter_emails = []  # No CC when in testing mode
        elif not primary_recruiter_email:
            # Kill switch is ON but no recruiter emails found - try to fall back to admin
            if admin_email:
                logger.warning(f"⚠️ No recruiter emails found for candidate {vetting_log.candidate_name} - falling back to admin email: {admin_email}")
                primary_recruiter_email = admin_email
                primary_recruiter_name = 'Admin'
                cc_recruiter_emails = []
            else:
                logger.warning(f"❌ No recruiter emails found and no admin email configured - cannot send notification for {vetting_log.candidate_name}")
                return 0

        # Best-effort resume attachment fetch — recruiter UX win so they can
        # keyword-search the resume from their inbox without opening Bullhorn.
        # Fail-open: any error here returns None and we send the email
        # without the attachment. Notification > convenience attachment.
        resume_attachments = self._fetch_resume_attachment(
            candidate_id=vetting_log.bullhorn_candidate_id,
            candidate_name=vetting_log.candidate_name,
        )

        # Send ONE email with primary as To: and others as CC:
        try:
            success = self._send_recruiter_email(
                recruiter_email=primary_recruiter_email,
                recruiter_name=primary_recruiter_name or '',
                candidate_name=vetting_log.candidate_name,
                candidate_id=vetting_log.bullhorn_candidate_id,
                matches=matches,
                cc_emails=cc_recruiter_emails,  # All other recruiters CC'd
                attachments=resume_attachments,
                applied_context_match=applied_context_match,
            )

            # Multi-recruiter resume attach observability (May 2026):
            # Recruiters reported intermittent missing-resume cases on multi-position
            # notifications. Log explicitly so we can quantify how often the
            # attachment was missing AND how many recruiters were on the thread,
            # making it trivial to grep prod for "team-thread without resume" cases.
            try:
                _recipient_count = 1 + len(cc_recruiter_emails or [])
                if resume_attachments:
                    logger.info(
                        f"📎 Resume attached to recruiter notification: "
                        f"recipients={_recipient_count} (1 to + {len(cc_recruiter_emails or [])} cc) "
                        f"candidate={vetting_log.bullhorn_candidate_id}"
                    )
                elif _recipient_count > 1:
                    logger.warning(
                        f"📎 Multi-recruiter notification sent WITHOUT resume attachment: "
                        f"recipients={_recipient_count} candidate={vetting_log.bullhorn_candidate_id} "
                        f"(check earlier 📎 log lines for fetch failure reason)"
                    )
            except Exception:
                pass  # Observability must never block the send

            if success:
                # Mark ALL matches as notified
                for match in matches:
                    match.notification_sent = True
                    match.notification_sent_at = datetime.utcnow()
                
                vetting_log.notifications_sent = True
                vetting_log.notification_count = 1  # One email sent to all
                db.session.commit()

                # Cross-revet dedupe ledger (Task #95) — record each
                # (candidate, job) pair we just emailed so the next
                # auditor re-vet can't fire a duplicate. Best-effort:
                # ledger failures are logged but never roll back the
                # already-successful send.
                _record_ledger_sent(
                    vetting_log.bullhorn_candidate_id,
                    [m.bullhorn_job_id for m in matches],
                    'qualified',
                )
                
                cc_info = f" (CC: {', '.join(cc_recruiter_emails)})" if cc_recruiter_emails else ""
                logger.info(f"Sent notification to {primary_recruiter_email}{cc_info} for {vetting_log.candidate_name} (Candidate ID: {vetting_log.bullhorn_candidate_id}, {len(matches)} positions)")
                
                # ── Scout Vetting trigger ──
                # After recruiter notification, initiate Scout Vetting for qualified matches
                try:
                    from scout_vetting_service import ScoutVettingService
                    sv_service = ScoutVettingService(email_service=self.email_service, bullhorn_service=self.bullhorn)
                    if sv_service.is_enabled():
                        sv_result = sv_service.initiate_vetting(vetting_log, matches)
                        logger.info(f"🔍 Scout Vetting initiated: {sv_result.get('created', 0)} sessions created, "
                                    f"{sv_result.get('queued', 0)} queued, {sv_result.get('skipped', 0)} skipped")
                except Exception as sv_err:
                    logger.error(f"Scout Vetting trigger error (non-blocking): {str(sv_err)}")
                
                return 1
            else:
                logger.error(f"Failed to send notification for {vetting_log.candidate_name} (Candidate ID: {vetting_log.bullhorn_candidate_id})")
                return 0
                
        except Exception as e:
            logger.error(f"Failed to send notification: {str(e)}")
            return 0
    
    def _fetch_resume_attachment(self, candidate_id: int,
                                 candidate_name: str) -> Optional[list]:
        """
        Best-effort resume fetch for inbox keyword search (May 2026).

        Returns a SendGrid-ready attachments list (one element) or None.
        Always fail-open: any exception, missing file, oversize file,
        or empty Bullhorn response returns None and the caller proceeds
        with no attachment. The email itself is the priority — we never
        want a Bullhorn hiccup to silently swallow a recruiter alert.

        Size cap: `_RESUME_ATTACHMENT_MAX_BYTES` (10MB). Resumes over the
        cap are skipped with an INFO log so we can monitor frequency.
        """
        if not candidate_id:
            return None
        try:
            file_content, original_filename = self.get_candidate_resume(candidate_id)
        except Exception as fetch_err:
            logger.warning(
                f"📎 Resume attach: fetch failed for candidate {candidate_id} "
                f"({type(fetch_err).__name__}: {fetch_err}); sending email without attachment"
            )
            return None

        if not file_content:
            logger.info(
                f"📎 Resume attach: no resume on file for candidate {candidate_id}; "
                f"sending email without attachment"
            )
            return None

        size = len(file_content)
        if size > _RESUME_ATTACHMENT_MAX_BYTES:
            logger.info(
                f"📎 Resume attach: candidate {candidate_id} resume is "
                f"{size / 1024 / 1024:.1f}MB (cap "
                f"{_RESUME_ATTACHMENT_MAX_BYTES / 1024 / 1024:.0f}MB); skipping attachment"
            )
            return None

        attachment = {
            'data': file_content,
            'filename': _safe_resume_filename(candidate_name, original_filename),
            'content_type': _resume_content_type(original_filename),
        }
        logger.info(
            f"📎 Resume attach: candidate {candidate_id} → {attachment['filename']} "
            f"({size} bytes, {attachment['content_type']})"
        )
        return [attachment]

    @staticmethod
    def _render_mirror_excerpts_html(details: dict) -> str:
        """Render the verbatim copied passage for the jd_mirror signal — shown as
        it appears in the resume AND as referenced in the job posting, with the
        copied span highlighted — so a recruiter can drill into exactly what was
        lifted and where (surfaced on all bands, including Clear).

        Returns '' when no passage was captured. All candidate- and posting-
        derived text is HTML-escaped; fail-soft so it never breaks the banner.
        """
        try:
            import html as _html
            resume_passage = str(details.get('copied_text') or '').strip()
            # The posting may render the same run with different casing/
            # punctuation; fall back to the resume passage if not captured.
            jd_passage = str(details.get('jd_passage') or '').strip() or resume_passage
            resume_ex = str(details.get('resume_excerpt') or '').strip()
            jd_ex = str(details.get('jd_excerpt') or '').strip()
            if not resume_passage or not (resume_ex or jd_ex):
                return ''

            def _hl(excerpt: str, passage: str) -> str:
                if not passage:
                    return _html.escape(excerpt)
                key = passage
                idx = excerpt.find(key)
                # A very long passage is stored ellipsized for display; the
                # excerpt still holds the untruncated text, so fall back to the
                # pre-ellipsis prefix to keep the highlight working.
                if idx < 0 and key.endswith('…'):
                    key = key[:-1].rstrip()
                    idx = excerpt.find(key)
                if idx < 0 or not key:
                    return _html.escape(excerpt)
                before = _html.escape(excerpt[:idx])
                mid = _html.escape(excerpt[idx:idx + len(key)])
                after = _html.escape(excerpt[idx + len(key):])
                return (f'{before}<mark style="background:#fff3cd; padding:0 2px;">'
                        f'{mid}</mark>{after}')

            rows = ''
            if resume_ex:
                rows += (
                    '<div style="margin:0 0 4px 0;">'
                    '<span style="color:#6c757d; font-weight:bold;">'
                    'In r&eacute;sum&eacute;:</span> '
                    f'&hellip;{_hl(resume_ex, resume_passage)}&hellip;</div>'
                )
            if jd_ex:
                rows += (
                    '<div>'
                    '<span style="color:#6c757d; font-weight:bold;">'
                    'In job posting:</span> '
                    f'&hellip;{_hl(jd_ex, jd_passage)}&hellip;</div>'
                )
            return (
                '<div style="margin:4px 0 2px 0; padding:6px 8px;'
                ' background:rgba(0,0,0,0.04); border-radius:4px;'
                ' font-size:12px; color:#495057; line-height:1.4;">'
                f'{rows}</div>'
            )
        except Exception:
            return ''

    def _build_fraud_banner_html(self, candidate_id: Optional[int]) -> str:
        """Advisory fraud-risk banner for recruiter notification emails.

        Mirrors the on-screen recruiter-portal badge: renders the candidate's
        latest fraud assessment for ALL bands — a green "Integrity Check Passed"
        summary for 'clear', amber for 'review', red for 'high_risk' — showing the
        band, the 0-100 score, and (for review/high_risk) the specific contributing
        signals. Clear candidates have no fired signals, so they show the static
        list of checks performed instead. Gated by the same `fraud_detection_enabled`
        flag that controls the whole feature (no separate toggle, per spec).

        Fail-soft by contract: any problem (feature off, no assessment, bad
        JSON, lookup error) returns '' so the notification email always sends.
        A fraud-lookup hiccup must NEVER block a recruiter alert.
        """
        try:
            if not candidate_id:
                return ''

            enabled = VettingConfig.query.filter_by(
                setting_key='fraud_detection_enabled'
            ).first()
            if not (enabled and (enabled.setting_value or '').lower() == 'true'):
                return ''

            from models import CandidateFraudAssessment
            assessment = (
                CandidateFraudAssessment.query
                .filter_by(bullhorn_candidate_id=candidate_id)
                .order_by(
                    CandidateFraudAssessment.created_at.desc(),
                    CandidateFraudAssessment.id.desc(),
                )
                .first()
            )
            if assessment is None:
                return ''

            band = (assessment.risk_band or '').lower()
            if band not in ('high_risk', 'review', 'clear'):
                return ''

            import html as _html
            import json as _json

            if band == 'high_risk':
                accent, bg, border = '#dc3545', '#fdecec', '#f5c2c7'
                label = '🚩 High Fraud Risk'
                disclaimer = ('Advisory only — automated integrity check. This does '
                              'not block screening; please apply your own judgement.')
            elif band == 'review':
                accent, bg, border = '#b7791f', '#fff8e1', '#f1d592'
                label = '⚠️ Fraud Risk — Review Recommended'
                disclaimer = ('Advisory only — automated integrity check. This does '
                              'not block screening; please apply your own judgement.')
            else:  # clear
                accent, bg, border = '#198754', '#e8f6ec', '#badbcc'
                label = '✅ Integrity Check Passed'
                disclaimer = ('Automated integrity check — no risk indicators detected '
                              'across the checks below. Advisory only.')

            score = assessment.risk_score or 0

            # Contributing-signal list (band + score + reasons, per spec).
            # Only signals that actually scored points are shown, ordered
            # highest-impact first and capped to keep the email scannable.
            # Every string is HTML-escaped — signal evidence can echo
            # candidate-supplied data.
            try:
                signals = _json.loads(assessment.signals_json or '[]')
            except (ValueError, TypeError):
                signals = []
            contributing = [
                s for s in signals
                if isinstance(s, dict) and (s.get('points') or 0) > 0
            ]
            contributing.sort(key=lambda s: s.get('points') or 0, reverse=True)
            # Informational (0-point) signals — never accusations, never scored
            # (e.g. AI-writing-style markers). Shown separately so a recruiter
            # never reads them as a risk indicator. Surfaced on ALL bands.
            informational = [
                s for s in signals
                if isinstance(s, dict) and (s.get('points') or 0) == 0
            ]

            reasons_html = ''
            if contributing:
                items = ''
                for s in contributing[:5]:
                    lbl = _html.escape(str(s.get('label') or s.get('code') or 'Risk signal'))
                    ev = s.get('evidence')
                    ev_html = (
                        f' — <span style="color:#6c757d;">{_html.escape(str(ev))}</span>'
                        if ev else ''
                    )
                    # For a verbatim-mirror hit, surface the actual copied
                    # passage (resume excerpt vs posting excerpt) so a recruiter
                    # can drill in — shown on ALL bands, including Clear.
                    detail_html = ''
                    if s.get('code') == 'jd_mirror':
                        detail_html = self._render_mirror_excerpts_html(s.get('details') or {})
                    items += f'<li style="margin:2px 0;">{lbl}{ev_html}{detail_html}</li>'
                reasons_html = (
                    '<ul style="margin:8px 0 0 0; padding-left:18px; '
                    f'font-size:13px; color:#495057;">{items}</ul>'
                )

            # Clear candidates have no fired signals — show the static list of
            # checks performed so the green banner still demonstrates coverage.
            if band == 'clear' and not reasons_html:
                _checks = [
                    'Email domain & address format',
                    'Contact-detail consistency',
                    'Work-history timeline',
                    'Resume-content uniqueness',
                    'Identity consistency',
                    'Profile near-duplicate check',
                    'Application velocity',
                    'LinkedIn profile reuse',
                    'Name completeness',
                    'Third-party-submission pattern',
                    'Job-description verbatim match',
                ]
                _items = ''.join(
                    f'<li style="margin:2px 0;">&#10003; {_html.escape(c)}</li>'
                    for c in _checks
                )
                reasons_html = (
                    '<ul style="margin:8px 0 0 0; padding-left:18px; list-style:none;'
                    f' font-size:13px; color:#495057;">{_items}</ul>'
                )

            # Muted informational note (all bands) — explicitly not a risk score.
            informational_html = ''
            if informational:
                _info_items = ''
                for s in informational[:3]:
                    lbl = _html.escape(str(s.get('label') or s.get('code') or 'Note'))
                    ev = s.get('evidence')
                    ev_html = f' — {_html.escape(str(ev))}' if ev else ''
                    _info_items += f'<li style="margin:2px 0;">{lbl}{ev_html}</li>'
                informational_html = (
                    '<p style="margin:8px 0 0 0; font-size:12px; color:#6c757d;'
                    ' font-style:italic;">Informational (not scored):</p>'
                    '<ul style="margin:2px 0 0 0; padding-left:18px; font-size:12px;'
                    f' color:#6c757d; font-style:italic;">{_info_items}</ul>'
                )

            return f"""
                <div style="background: {bg}; border: 1px solid {border};
                            border-left: 4px solid {accent}; border-radius: 6px;
                            padding: 12px 14px; margin: 0 0 15px 0;">
                    <p style="margin: 0; color: {accent}; font-weight: bold; font-size: 14px;">
                        {label} &nbsp;·&nbsp; Risk Score {score}/100
                    </p>
                    <p style="margin: 6px 0 0 0; color: #6c757d; font-size: 12px;">
                        {disclaimer}
                    </p>
                    {reasons_html}
                    {informational_html}
                </div>
            """
        except Exception as e:
            logger.warning(
                f"Fraud banner skipped for candidate {candidate_id}: {e}"
            )
            return ''

    def _send_recruiter_email(self, recruiter_email: str, recruiter_name: str,
                               candidate_name: str, candidate_id: int,
                               matches: List[CandidateJobMatch],
                               cc_emails: list = None,
                               attachments: Optional[list] = None,
                               applied_context_match: Optional['CandidateJobMatch'] = None) -> bool:
        """
        Send notification email to a recruiter about a qualified candidate.
        
        TRANSPARENCY MODEL: ONE email is sent with the primary recruiter as To:
        and all other recruiters CC'd on the same thread. Each job card shows
        which recruiter owns it for complete visibility.
        """
        # Build Bullhorn candidate URL (using cls45 subdomain for Bullhorn One)
        candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
        
        # Build transparency header if there are CC'd recruiters
        transparency_note = ""
        if cc_emails and len(cc_emails) > 0:
            transparency_note = f"""
                <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                    <p style="margin: 0; color: #1565c0; font-size: 13px;">
                        <strong>📢 Team Thread:</strong> This candidate matches multiple positions.
                        CC'd on this email: <em>{', '.join(cc_emails)}</em>
                    </p>
                </div>
            """
        
        # Build email content — job-aware subject (Option A) so recruiters can
        # triage from the inbox without opening every Scout email (May 2026).
        subject = _build_recruiter_subject(candidate_name, matches)
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 24px;">🎯 Qualified Candidate Match</h1>
            </div>
            
            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef;">
                <p style="margin: 0 0 15px 0;">Hi {recruiter_name or 'there'},</p>
                
                {transparency_note}
                
                <p style="margin: 0 0 15px 0;">
                    A new candidate has been analyzed by Scout Screening and matches 
                    <strong>{len(matches)} position(s)</strong>.
                </p>
                
                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 20px 0;">
                    <h2 style="margin: 0 0 10px 0; color: #495057; font-size: 18px;">
                        👤 {candidate_name}
                    </h2>
                    <a href="{candidate_url}" 
                       style="display: inline-block; background: #667eea; color: white; 
                              padding: 10px 20px; border-radius: 5px; text-decoration: none;
                              margin-top: 10px;">
                        View Candidate Profile →
                    </a>
                </div>
                
                {self._build_fraud_banner_html(candidate_id)}

                <h3 style="color: #495057; margin: 20px 0 10px 0;">Matched Positions:</h3>
        """
        
        for match in matches:
            applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if match.is_applied_job else ''
            job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={match.bullhorn_job_id}"
            
            # Show recruiter ownership for each job
            recruiter_tag = ""
            if match.recruiter_name:
                is_your_job = match.recruiter_email == recruiter_email
                if is_your_job:
                    recruiter_tag = f'<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">YOUR JOB</span>'
                else:
                    recruiter_tag = f'<span style="background: #6c757d; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">{match.recruiter_name}\'s Job</span>'
            
            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px; 
                            border-left: 4px solid #28a745; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #28a745;">
                        <a href="{job_url}" style="color: #28a745; text-decoration: none;">{match.job_title} (Job ID: {match.bullhorn_job_id})</a>{applied_badge}{recruiter_tag}
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px;">
                        <strong>Match Score:</strong> {match.match_score:.0f}%
                    </div>
                    <p style="margin: 0; color: #495057;">{match.match_summary}</p>
                    
                    {f'<p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {match.skills_match}</p>' if match.skills_match else ''}
                </div>
            """
        
        # ── Applied-job context block (May 2026) ──
        # Renders ONLY when the candidate didn't qualify for the job they
        # actually applied to (so the matched jobs above are all "related
        # roles"). Gives recruiters the conversational opening they need:
        # "I see you applied to X — you didn't quite hit the bar there, but
        # you're a strong fit for Y." Compact one-liner per user request,
        # not a full summary, to keep the email scannable.
        if applied_context_match is not None:
            # Defensive HTML escaping on AI/Bullhorn-sourced strings (May 2026
            # architect-review hardening). The matched-position cards above
            # don't currently escape — but rather than perpetuate the same
            # surface, harden the new block. job_title and match_summary
            # originate from Bullhorn job records and AI-generated text;
            # both could theoretically contain `<`, `>`, or `&` characters.
            import html as _html
            _ctx_job_id = int(applied_context_match.bullhorn_job_id or 0)
            _ctx_job_title = _html.escape(applied_context_match.job_title or 'Position')
            _ctx_job_url = (
                f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/"
                f"OpenWindow.cfm?Entity=JobOrder&id={_ctx_job_id}"
            )
            _ctx_summary_raw = (applied_context_match.match_summary or '').strip()
            # Compact one-liner: cap at ~220 chars, ellipsize gracefully
            _ctx_summary_truncated = (
                _ctx_summary_raw if len(_ctx_summary_raw) <= 220
                else _ctx_summary_raw[:217].rsplit(' ', 1)[0] + '…'
            )
            _ctx_summary = _html.escape(_ctx_summary_truncated)
            _ctx_score = (applied_context_match.match_score or 0)
            html_content += f"""
                <h3 style="color: #495057; margin: 25px 0 10px 0;">
                    📥 Job They Originally Applied To
                </h3>
                <div style="background: #fff8e1; padding: 15px; border-radius: 8px;
                            border-left: 4px solid #f9a825; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #5d4037;">
                        <a href="{_ctx_job_url}" style="color: #5d4037; text-decoration: none;">
                            {_ctx_job_title} (Job ID: {_ctx_job_id})
                        </a>
                        <span style="background: #6c757d; color: white; padding: 2px 8px;
                                     border-radius: 3px; font-size: 11px; margin-left: 8px;">
                            BELOW THRESHOLD
                        </span>
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px; font-size: 13px;">
                        <strong>Match Score:</strong> {_ctx_score:.0f}% &nbsp;·&nbsp;
                        <em>This candidate didn't qualify for the role they applied to —
                        the matched position(s) above are stronger fits via related-role logic.
                        Useful framing for your outreach call.</em>
                    </div>
                    {f'<p style="margin: 0; color: #495057; font-size: 13px;">{_ctx_summary}</p>' if _ctx_summary else ''}
                </div>
            """

        html_content += f"""
                <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        <strong>Recommended Action:</strong> Review the candidate's profile and 
                        reach out if they're a good fit for your open position(s).
                    </p>
                </div>
            </div>
            
            <div style="background: #343a40; color: #adb5bd; padding: 15px; 
                        border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
                Powered by Scout Screening™ • Myticas Consulting
            </div>
        </div>
        """
        
        # Send the email with CC recipients and BCC admin for transparency
        try:
            # Always BCC admin for monitoring/troubleshooting
            admin_bcc_email = 'kroots@myticas.com'
            
            job_titles = ', '.join(set(m.job_title for m in matches if m.job_title)) or 'unknown position'
            avg_score = sum(m.match_score for m in matches) / len(matches) if matches else 0
            changes_summary = f"Screening alert — {candidate_name} matched {job_titles} (Score: {avg_score:.0f}%)"
            result = self.email_service.send_html_email(
                to_email=recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_recruiter_notification',
                cc_emails=cc_emails,  # CC all other recruiters on same thread
                bcc_emails=[admin_bcc_email],  # BCC admin for transparency
                changes_summary=changes_summary,
                attachments=attachments,  # Resume PDF/DOCX (best-effort)
            )
            return result is True or (isinstance(result, dict) and result.get('success', False))
        except Exception as e:
            logger.error(f"Email send error: {str(e)}")
            return False

    def _send_prestige_review_notification(self, vetting_log: CandidateVettingLog) -> int:
        prestige_matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            notification_sent=False,
            prestige_boost_applied=True,
        ).filter(
            CandidateJobMatch.prestige_employer.isnot(None),
            CandidateJobMatch.prestige_employer != '',
        ).all()

        if not prestige_matches:
            return 0

        # ── Threshold gate ──────────────────────────────────────────────
        # The +5 prestige bump is a courtesy boost for candidates currently
        # at Tier-1 firms. The recruiter should ONLY be notified when the
        # bumped final score actually meets or exceeds the qualifying
        # threshold. If the candidate still falls below threshold even with
        # the +5, they are a genuine Not-Recommended result and should not
        # generate noise — same rule as the standard qualified path.
        global_threshold = self.get_threshold()
        job_threshold_map = {}
        job_ids = [m.bullhorn_job_id for m in prestige_matches if m.bullhorn_job_id]
        if job_ids:
            try:
                from models import JobVettingRequirements
                custom_reqs = JobVettingRequirements.query.filter(
                    JobVettingRequirements.bullhorn_job_id.in_(job_ids),
                    JobVettingRequirements.vetting_threshold.isnot(None),
                ).all()
                for req in custom_reqs:
                    job_threshold_map[req.bullhorn_job_id] = float(req.vetting_threshold)
            except Exception as e:
                logger.warning(
                    f"Could not fetch per-job thresholds for prestige review: {str(e)}"
                )

        qualified_prestige_matches = [
            m for m in prestige_matches
            if (m.match_score or 0) >= resolve_match_threshold(
                m, job_threshold_map, global_threshold
            )
        ]

        if not qualified_prestige_matches:
            logger.info(
                f"  🏢 Skipping prestige notification for {vetting_log.candidate_name}: "
                f"{len(prestige_matches)} prestige match(es) but none cleared the "
                f"qualifying threshold even with the +5 bump (highest score: "
                f"{max((m.match_score or 0) for m in prestige_matches):.0f}%)"
            )
            return 0

        prestige_matches = qualified_prestige_matches
        logger.info(f"  🏢 Found {len(prestige_matches)} prestige employer matches for not-qualified candidate {vetting_log.candidate_name}")

        # Cross-revet dedupe (Task #95) — see send_recruiter_notifications.
        prestige_matches, suppressed_prestige = _filter_matches_by_ledger(
            prestige_matches, vetting_log.bullhorn_candidate_id, 'prestige',
        )
        if suppressed_prestige:
            now = datetime.utcnow()
            for m in suppressed_prestige:
                m.notification_sent = True
                m.notification_sent_at = now
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        if not prestige_matches:
            logger.info(
                f"  ⏭️ Skipping prestige notification for "
                f"{vetting_log.candidate_name} — already emailed within "
                f"the last {_RECRUITER_NOTIFICATION_DEDUPE_WINDOW_HOURS}h "
                f"(suppressed={len(suppressed_prestige)})"
            )
            return 0

        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []

        for match in prestige_matches:
            if match.is_applied_job and match.recruiter_email:
                emails = parse_emails(match.recruiter_email)
                names = parse_names(match.recruiter_name)
                if emails:
                    primary_recruiter_email = emails[0]
                    primary_recruiter_name = names[0] if names else ''
                break

        seen_emails = set()
        for match in prestige_matches:
            emails = parse_emails(match.recruiter_email)
            names = parse_names(match.recruiter_name)
            for i, email in enumerate(emails):
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    name = names[i] if i < len(names) else ''
                    if not primary_recruiter_email:
                        primary_recruiter_email = email
                        primary_recruiter_name = name
                    elif email != primary_recruiter_email:
                        cc_recruiter_emails.append(email)

        send_setting = VettingConfig.query.filter_by(setting_key='send_recruiter_emails').first()
        send_to_recruiters = send_setting and send_setting.setting_value.lower() == 'true'

        admin_setting = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
        admin_email = admin_setting.setting_value if admin_setting and admin_setting.setting_value else ''

        if not send_to_recruiters:
            if not admin_email:
                logger.warning(f"❌ Prestige notification blocked — recruiter emails disabled and no admin email")
                return 0
            primary_recruiter_email = admin_email
            primary_recruiter_name = 'Admin'
            cc_recruiter_emails = []
        elif not primary_recruiter_email:
            if admin_email:
                primary_recruiter_email = admin_email
                primary_recruiter_name = 'Admin'
                cc_recruiter_emails = []
            else:
                return 0

        prestige_firm = prestige_matches[0].prestige_employer
        candidate_name = vetting_log.candidate_name
        candidate_id = vetting_log.bullhorn_candidate_id
        candidate_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=Candidate&id={candidate_id}"
        highest_score = max(m.match_score for m in prestige_matches)

        subject = f"🏢 Prestige Review: {candidate_name} — Currently at {prestige_firm}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 22px;">🏢 Prestige Employer — Review Recommended</h1>
            </div>
            
            <div style="background: #fff8e1; padding: 15px 20px; border-left: 4px solid #f9a825; border-right: 1px solid #e9ecef;">
                <p style="margin: 0; color: #5d4037; font-size: 14px;">
                    <strong>⚠️ Below Threshold — But Worth Reviewing</strong><br>
                    This candidate scored below the qualified threshold (<strong>{highest_score:.0f}%</strong>);
                    however, they are currently employed at <strong>{prestige_firm}</strong>.
                    Their resume may not fully reflect their skills and experience.
                    <strong>Recruiter review is recommended.</strong>
                </p>
            </div>

            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef; border-top: none;">
                <p style="margin: 0 0 15px 0;">Hi {primary_recruiter_name or 'there'},</p>

                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 15px 0;">
                    <h2 style="margin: 0 0 5px 0; color: #495057; font-size: 18px;">
                        👤 {candidate_name}
                    </h2>
                    <p style="margin: 0 0 10px 0; color: #6c757d; font-size: 13px;">
                        🏢 Currently at <strong style="color: #1e3a5f;">{prestige_firm}</strong>
                    </p>
                    <a href="{candidate_url}"
                       style="display: inline-block; background: #1e3a5f; color: white;
                              padding: 10px 20px; border-radius: 5px; text-decoration: none;">
                        View Candidate Profile →
                    </a>
                </div>

                {self._build_fraud_banner_html(candidate_id)}

                <h3 style="color: #495057; margin: 20px 0 10px 0;">Screening Results:</h3>
        """

        for match in prestige_matches:
            job_url = f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm?Entity=JobOrder&id={match.bullhorn_job_id}"
            applied_badge = '<span style="background: #ffc107; color: #000; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>' if match.is_applied_job else ''
            boost_badge = ''
            if match.prestige_boost_applied:
                boost_badge = '<span style="background: #e3f2fd; color: #1565c0; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-left: 8px;">+5 PRESTIGE</span>'

            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px;
                            border-left: 4px solid #f9a825; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #5d4037;">
                        <a href="{job_url}" style="color: #5d4037; text-decoration: none;">{match.job_title} (Job ID: {match.bullhorn_job_id})</a>{applied_badge}{boost_badge}
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px;">
                        <strong>Match Score:</strong> {match.match_score:.0f}%
                    </div>
                    <p style="margin: 0; color: #495057;">{match.match_summary}</p>
                    {f'<p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {match.skills_match}</p>' if match.skills_match else ''}
                </div>
            """

        html_content += f"""
                <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        <strong>Why this alert?</strong> Candidates at major consulting firms often have
                        broader experience than what appears on their resume. This candidate may be
                        a strong fit despite the score — a quick profile review is recommended.
                    </p>
                </div>
            </div>

            <div style="background: #343a40; color: #adb5bd; padding: 15px;
                        border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
                Powered by Scout Screening™ • Myticas Consulting
            </div>
        </div>
        """

        try:
            admin_bcc_email = 'kroots@myticas.com'
            job_titles = ', '.join(set(m.job_title for m in prestige_matches if m.job_title)) or 'unknown'
            changes_summary = f"Prestige review alert — {candidate_name} at {prestige_firm}, matched {job_titles} (Score: {highest_score:.0f}%)"
            result = self.email_service.send_html_email(
                to_email=primary_recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_prestige_notification',
                cc_emails=cc_recruiter_emails,
                bcc_emails=[admin_bcc_email],
                changes_summary=changes_summary
            )
            if result is True or (isinstance(result, dict) and result.get('success', False)):
                for match in prestige_matches:
                    match.notification_sent = True
                    match.notification_sent_at = datetime.utcnow()
                db.session.commit()
                # Cross-revet dedupe ledger (Task #95).
                _record_ledger_sent(
                    vetting_log.bullhorn_candidate_id,
                    [m.bullhorn_job_id for m in prestige_matches],
                    'prestige',
                )
                logger.info(f"  🏢 Prestige review notification sent to {primary_recruiter_email} for {candidate_name}")
                return 1
            return 0
        except Exception as e:
            logger.error(f"Prestige notification send error: {str(e)}")
            return 0

    def _send_location_review_notification(self, vetting_log: CandidateVettingLog) -> int:
        """
        Send a recruiter notification for the LOCATION REVIEW tier.

        Fires when a candidate is not_qualified (final score below threshold)
        but their technical fit met or exceeded the threshold and only a small
        location penalty (≤ 15 pts) — or a legacy AI-flagged hard barrier within
        the 15-pt buffer — knocked them under. The recruiter should make the
        call rather than the system silently rejecting.

        Honors the same `send_recruiter_emails` kill-switch and
        `admin_notification_email` fallback as the qualified-candidate path,
        with a distinct subject line so recruiters can filter or sort.
        """
        threshold = self.get_threshold()

        candidate_matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id,
            notification_sent=False,
            is_qualified=False,
        ).all()

        # Build per-job threshold map so location-review eligibility is evaluated
        # against the same threshold that determined each match's is_qualified
        # status (keeps the new tier consistent with per-job custom thresholds).
        job_threshold_map = {}
        job_ids = [m.bullhorn_job_id for m in candidate_matches if m.bullhorn_job_id]
        if job_ids:
            try:
                from models import JobVettingRequirements
                custom_reqs = JobVettingRequirements.query.filter(
                    JobVettingRequirements.bullhorn_job_id.in_(job_ids),
                    JobVettingRequirements.vetting_threshold.isnot(None),
                ).all()
                for req in custom_reqs:
                    job_threshold_map[req.bullhorn_job_id] = float(req.vetting_threshold)
            except Exception as e:
                logger.warning(f"Could not fetch per-job thresholds for location review: {str(e)}")

        location_matches = [
            m for m in candidate_matches
            if is_location_review_match(
                m, resolve_match_threshold(m, job_threshold_map, threshold)
            )
        ]

        if not location_matches:
            return 0

        logger.info(
            f"  📍 Found {len(location_matches)} location-review match(es) for "
            f"not-qualified candidate {vetting_log.candidate_name}"
        )

        # Cross-revet dedupe (Task #95) — see send_recruiter_notifications.
        location_matches, suppressed_loc = _filter_matches_by_ledger(
            location_matches, vetting_log.bullhorn_candidate_id, 'location_review',
        )
        if suppressed_loc:
            now = datetime.utcnow()
            for m in suppressed_loc:
                m.notification_sent = True
                m.notification_sent_at = now
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        if not location_matches:
            logger.info(
                f"  ⏭️ Skipping location-review notification for "
                f"{vetting_log.candidate_name} — already emailed within "
                f"the last {_RECRUITER_NOTIFICATION_DEDUPE_WINDOW_HOURS}h "
                f"(suppressed={len(suppressed_loc)})"
            )
            return 0

        # ── Per-recruiter Location-Review opt-out (May 2026) ──
        # Load explicit OFF prefs for the jobs in this candidate's matches.
        # Default is ON; only explicit OFF rows live in the table. Fail-open:
        # any error here drops back to the original "send to everyone" path.
        disabled_emails_by_job = {}
        try:
            from models import RecruiterNotificationPref, User
            from extensions import db as _db
            job_ids_in_matches = list({
                m.bullhorn_job_id for m in location_matches if m.bullhorn_job_id
            })
            if job_ids_in_matches:
                pref_rows = (
                    _db.session.query(
                        RecruiterNotificationPref.bullhorn_job_id, User.email
                    )
                    .join(User, User.id == RecruiterNotificationPref.user_id)
                    .filter(
                        RecruiterNotificationPref.bullhorn_job_id.in_(job_ids_in_matches),
                        RecruiterNotificationPref.notification_type == 'location_review',
                        RecruiterNotificationPref.enabled.is_(False),
                    )
                    .all()
                )
                for jid, email in pref_rows:
                    if email:
                        disabled_emails_by_job.setdefault(jid, set()).add(
                            email.strip().lower()
                        )
        except Exception as e:
            logger.warning(
                f"location_review pref lookup failed (fail-open): {e}"
            )
            disabled_emails_by_job = {}

        def _is_opted_out(email_str, job_id):
            if not email_str or not job_id:
                return False
            return email_str.strip().lower() in disabled_emails_by_job.get(job_id, set())

        filtered_out_count = 0

        # ── Recruiter resolution (mirrors qualified-candidate flow) ──
        primary_recruiter_email = None
        primary_recruiter_name = None
        cc_recruiter_emails = []

        for match in location_matches:
            if match.is_applied_job and match.recruiter_email:
                emails = parse_emails(match.recruiter_email)
                names = parse_names(match.recruiter_name)
                # Pick the first email on the applied-job match that hasn't
                # opted out of location_review for this specific job.
                for i, em in enumerate(emails):
                    if em and not _is_opted_out(em, match.bullhorn_job_id):
                        primary_recruiter_email = em
                        primary_recruiter_name = names[i] if i < len(names) else ''
                        break
                    elif em:
                        filtered_out_count += 1
                if primary_recruiter_email:
                    break

        seen_emails = set()
        for match in location_matches:
            emails = parse_emails(match.recruiter_email)
            names = parse_names(match.recruiter_name)
            for i, email in enumerate(emails):
                if not email or email in seen_emails:
                    continue
                seen_emails.add(email)
                if _is_opted_out(email, match.bullhorn_job_id):
                    filtered_out_count += 1
                    continue
                name = names[i] if i < len(names) else ''
                if not primary_recruiter_email:
                    primary_recruiter_email = email
                    primary_recruiter_name = name
                elif email != primary_recruiter_email:
                    cc_recruiter_emails.append(email)

        if filtered_out_count:
            logger.info(
                f"event=location_review_pref_filtered "
                f"candidate={vetting_log.bullhorn_candidate_id} "
                f"filtered={filtered_out_count} "
                f"remaining_primary={'yes' if primary_recruiter_email else 'no'} "
                f"remaining_cc={len(cc_recruiter_emails)}"
            )

        # ── Kill-switch + admin fallback (same as Qualified path) ──
        send_setting = VettingConfig.query.filter_by(setting_key='send_recruiter_emails').first()
        send_to_recruiters = bool(send_setting) and send_setting.setting_value.lower() == 'true'

        admin_setting = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
        admin_email = admin_setting.setting_value if admin_setting and admin_setting.setting_value else ''

        if not send_to_recruiters:
            if not admin_email:
                logger.warning(
                    f"❌ Location-review notification blocked — recruiter emails disabled "
                    f"and no admin email configured for {vetting_log.candidate_name}"
                )
                return 0
            primary_recruiter_email = admin_email
            primary_recruiter_name = 'Admin'
            cc_recruiter_emails = []
        elif not primary_recruiter_email:
            # Distinguish "no recruiters at all" (admin fallback) from
            # "all recruiters explicitly opted out" (silent drop — they
            # asked us not to send these).
            if filtered_out_count and not admin_email:
                logger.info(
                    f"📍 Location-review notification suppressed — all "
                    f"recipients opted out for {vetting_log.candidate_name} "
                    f"(filtered={filtered_out_count})"
                )
                return 0
            if filtered_out_count:
                logger.info(
                    f"📍 Location-review — all recruiters opted out for "
                    f"{vetting_log.candidate_name}; not falling back to admin"
                )
                return 0
            if admin_email:
                logger.warning(
                    f"⚠️ No recruiter emails on location-review matches for {vetting_log.candidate_name} "
                    f"— falling back to admin: {admin_email}"
                )
                primary_recruiter_email = admin_email
                primary_recruiter_name = 'Admin'
                cc_recruiter_emails = []
            else:
                return 0

        # ── Build email content ──
        candidate_name = vetting_log.candidate_name
        candidate_id = vetting_log.bullhorn_candidate_id
        candidate_url = (
            f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm"
            f"?Entity=Candidate&id={candidate_id}"
        )

        # Pick the strongest technical fit among the location-review matches for the subject
        top_match = max(
            location_matches,
            key=lambda m: (m.technical_score or m.match_score or 0),
        )
        top_tech = top_match.technical_score or top_match.match_score or 0
        top_final = top_match.match_score or 0
        top_job_title = (getattr(top_match, 'job_title', None) or 'Position').strip() or 'Position'
        top_job_id = getattr(top_match, 'bullhorn_job_id', None)

        # Subject mirrors the qualified-email pattern (Job #{ID} + "+N more")
        # while preserving the "📍 Location Review:" prefix so existing inbox
        # filters/rules keep working. May 2026 — added Job ID + multi-match suffix.
        if top_job_id:
            subject_head = (
                f"📍 Location Review: {candidate_name} — {top_job_title} (Job #{top_job_id}) — "
                f"{top_tech:.0f}% Tech Fit → {top_final:.0f}% after location"
            )
        else:
            subject_head = (
                f"📍 Location Review: {candidate_name} — {top_job_title} — "
                f"{top_tech:.0f}% Tech Fit → {top_final:.0f}% after location"
            )
        extra_matches = len(location_matches) - 1
        subject = (
            f"{subject_head} +{extra_matches} more" if extra_matches > 0 else subject_head
        )

        transparency_note = ""
        if cc_recruiter_emails:
            transparency_note = f"""
                <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                    <p style="margin: 0; color: #1565c0; font-size: 13px;">
                        <strong>📢 Team Thread:</strong> CC'd on this email:
                        <em>{', '.join(cc_recruiter_emails)}</em>
                    </p>
                </div>
            """

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #2c5364 0%, #203a43 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h1 style="margin: 0; font-size: 22px;">📍 Location Review — Recruiter Judgment Needed</h1>
            </div>

            <div style="background: #fff8e1; padding: 15px 20px; border-left: 4px solid #f9a825; border-right: 1px solid #e9ecef;">
                <p style="margin: 0; color: #5d4037; font-size: 14px;">
                    <strong>⚠️ Strong Technical Fit — Below Threshold Due to Location</strong><br>
                    This candidate's <strong>technical fit ({top_tech:.0f}%)</strong> meets or exceeds
                    the {threshold:.0f}% qualifying threshold. A location penalty brought their
                    final score to <strong>{top_final:.0f}%</strong>. They are being surfaced for
                    your review rather than auto-rejected — please weigh commute, relocation, or
                    hybrid logistics before deciding.
                </p>
            </div>

            <div style="background: #f8f9fa; padding: 20px; border: 1px solid #e9ecef; border-top: none;">
                <p style="margin: 0 0 15px 0;">Hi {primary_recruiter_name or 'there'},</p>

                {transparency_note}

                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; margin: 15px 0;">
                    <h2 style="margin: 0 0 10px 0; color: #495057; font-size: 18px;">
                        👤 {candidate_name}
                    </h2>
                    <a href="{candidate_url}"
                       style="display: inline-block; background: #2c5364; color: white;
                              padding: 10px 20px; border-radius: 5px; text-decoration: none;">
                        View Candidate Profile →
                    </a>
                </div>

                {self._build_fraud_banner_html(candidate_id)}

                <h3 style="color: #495057; margin: 20px 0 10px 0;">Position(s) Affected:</h3>
        """

        for match in location_matches:
            job_url = (
                f"https://cls45.bullhornstaffing.com/BullhornSTAFFING/OpenWindow.cfm"
                f"?Entity=JobOrder&id={match.bullhorn_job_id}"
            )
            applied_badge = (
                '<span style="background: #ffc107; color: #000; padding: 2px 8px; '
                'border-radius: 3px; font-size: 11px; margin-left: 8px;">APPLIED</span>'
                if match.is_applied_job else ''
            )
            tech = match.technical_score or match.match_score or 0
            final = match.match_score or 0
            score_block = (
                f"<strong>Technical Fit:</strong> {tech:.0f}% &nbsp;→&nbsp; "
                f"<strong>Final (after location):</strong> {final:.0f}%"
                if tech and tech != final
                else f"<strong>Match Score:</strong> {final:.0f}%"
            )
            html_content += f"""
                <div style="background: white; padding: 15px; border-radius: 8px;
                            border-left: 4px solid #2c5364; margin: 10px 0;">
                    <h4 style="margin: 0 0 8px 0; color: #2c5364;">
                        <a href="{job_url}" style="color: #2c5364; text-decoration: none;">{match.job_title} (Job ID: {match.bullhorn_job_id})</a>{applied_badge}
                    </h4>
                    <div style="color: #6c757d; margin-bottom: 8px;">
                        {score_block}
                    </div>
                    <p style="margin: 0; color: #495057;">{match.match_summary}</p>
                    {f'<p style="margin: 10px 0 0 0; color: #495057;"><strong>Key Skills:</strong> {match.skills_match}</p>' if match.skills_match else ''}
                </div>
            """

        html_content += f"""
                <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #dee2e6;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        <strong>Why this alert?</strong> The screening engine treats a candidate
                        whose technical fit is at or above threshold but whose final score was
                        reduced by a small location penalty (≤ {top_tech - top_final:.0f} pts here)
                        as a recruiter judgment call rather than an automatic rejection.
                    </p>
                </div>
            </div>

            <div style="background: #343a40; color: #adb5bd; padding: 15px;
                        border-radius: 0 0 8px 8px; font-size: 12px; text-align: center;">
                Powered by Scout Screening™ • Myticas Consulting
            </div>
        </div>
        """

        # ── Send ──
        try:
            # Best-effort resume attachment — same fail-open contract as the
            # qualified-candidate path (May 2026). Notification > attachment.
            resume_attachments = self._fetch_resume_attachment(
                candidate_id=candidate_id,
                candidate_name=candidate_name,
            )

            admin_bcc_email = 'kroots@myticas.com'
            job_titles = ', '.join(set(m.job_title for m in location_matches if m.job_title)) or 'unknown'
            changes_summary = (
                f"Location review alert — {candidate_name}: "
                f"{top_tech:.0f}% technical fit on {job_titles}, "
                f"final {top_final:.0f}% after location penalty"
            )
            result = self.email_service.send_html_email(
                to_email=primary_recruiter_email,
                subject=subject,
                html_content=html_content,
                notification_type='vetting_location_review_notification',
                cc_emails=cc_recruiter_emails,
                bcc_emails=[admin_bcc_email],
                changes_summary=changes_summary,
                attachments=resume_attachments,
            )
            if result is True or (isinstance(result, dict) and result.get('success', False)):
                for match in location_matches:
                    match.notification_sent = True
                    match.notification_sent_at = datetime.utcnow()
                db.session.commit()
                # Cross-revet dedupe ledger (Task #95).
                _record_ledger_sent(
                    vetting_log.bullhorn_candidate_id,
                    [m.bullhorn_job_id for m in location_matches],
                    'location_review',
                )
                logger.info(
                    f"  📍 Location review notification sent to {primary_recruiter_email} "
                    f"for {candidate_name} ({len(location_matches)} match(es))"
                )
                return 1
            return 0
        except Exception as e:
            logger.error(f"Location review notification send error: {str(e)}")
            return 0

