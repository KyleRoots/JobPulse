"""Recruiter notification retry, duplicate-merge bulk scan, and admin notification email helper.

Part of the `automation_service` package — the monolithic
`automation_service.py` (1,839 lines) was split into focused mixins so
each cluster of related builtins lives next to its helpers.
"""
import json
import logging
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

logger = logging.getLogger(__name__)


class NotificationsMixin:
    def _builtin_retry_recruiter_notifications(self, params):
        from models import CandidateVettingLog
        from candidate_vetting_service import CandidateVettingService

        dry_run = params.get("dry_run", False)
        since_date_str = params.get("since_date", "")

        if since_date_str:
            try:
                since_dt = datetime.strptime(since_date_str, "%Y-%m-%d")
            except ValueError:
                since_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            since_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        query = CandidateVettingLog.query.filter(
            CandidateVettingLog.is_qualified == True,
            CandidateVettingLog.notifications_sent == False,
            CandidateVettingLog.created_at >= since_dt,
        ).order_by(CandidateVettingLog.created_at.asc())

        logs = query.all()
        total = len(logs)

        if total == 0:
            return {
                "summary": f"No unnotified qualified candidates found since {since_dt.strftime('%Y-%m-%d')}.",
                "total_found": 0,
                "sent": 0,
                "failed": 0,
                "dry_run": dry_run,
            }

        if dry_run:
            names = [f"{l.candidate_name} (ID: {l.bullhorn_candidate_id})" for l in logs[:10]]
            return {
                "summary": (
                    f"Dry run — {total} qualified candidate(s) with no notification sent since "
                    f"{since_dt.strftime('%Y-%m-%d')}: {', '.join(names)}"
                    + (" (+ more)" if total > 10 else "")
                ),
                "total_found": total,
                "sent": 0,
                "failed": 0,
                "dry_run": True,
                "candidates": [
                    {"name": l.candidate_name, "id": l.bullhorn_candidate_id, "created_at": str(l.created_at)}
                    for l in logs
                ],
            }

        vetting_svc = CandidateVettingService(bullhorn_service=self.bullhorn)

        sent = 0
        failed = 0
        sent_names = []
        failed_names = []

        for vetting_log in logs:
            try:
                count = vetting_svc.send_recruiter_notifications(vetting_log)
                if count > 0:
                    sent += 1
                    sent_names.append(f"{vetting_log.candidate_name} (ID: {vetting_log.bullhorn_candidate_id})")
                    self.logger.info(f"retry_recruiter_notifications: sent for {vetting_log.candidate_name}")
                else:
                    failed += 1
                    failed_names.append(f"{vetting_log.candidate_name} (ID: {vetting_log.bullhorn_candidate_id})")
                    self.logger.warning(f"retry_recruiter_notifications: send_recruiter_notifications returned 0 for {vetting_log.candidate_name}")
            except Exception as e:
                failed += 1
                failed_names.append(f"{vetting_log.candidate_name}")
                self.logger.error(f"retry_recruiter_notifications: error for {vetting_log.candidate_name}: {e}")

        summary_parts = [f"Found {total} unnotified qualified candidate(s) since {since_dt.strftime('%Y-%m-%d')}."]
        if sent:
            summary_parts.append(f"{sent} notification(s) sent: {', '.join(sent_names[:5])}" + (" (+ more)" if sent > 5 else ""))
        if failed:
            summary_parts.append(f"{failed} failed: {', '.join(failed_names[:5])}")

        return {
            "summary": " ".join(summary_parts),
            "total_found": total,
            "sent": sent,
            "failed": failed,
            "dry_run": False,
            "sent_names": sent_names,
            "failed_names": failed_names,
        }

    def _builtin_duplicate_merge_scan(self, params):
        from duplicate_merge_service import DuplicateMergeService

        merge_service = DuplicateMergeService()

        def progress_cb(stats):
            logger.info(f"🔀 Bulk merge progress: scanned={stats['candidates_scanned']}, merged={stats['merged']}")

        status = "completed"
        error_detail = None
        try:
            stats = merge_service.run_bulk_scan(progress_callback=progress_cb)
        except Exception as e:
            status = "failed"
            error_detail = str(e)
            stats = {
                'candidates_scanned': 0, 'duplicates_found': 0,
                'merged': 0, 'skipped_both_placements': 0,
                'skipped_below_threshold': 0, 'errors': 1,
            }
            logger.error(f"🔀 Bulk merge scan failed: {e}", exc_info=True)

        self._send_bulk_scan_notification(stats, status, error_detail)

        summary = (
            f"Bulk duplicate scan complete. "
            f"Scanned {stats['candidates_scanned']} candidates, "
            f"found {stats['duplicates_found']} duplicates, "
            f"merged {stats['merged']}, "
            f"skipped (placement conflict) {stats['skipped_both_placements']}, "
            f"errors {stats['errors']}."
        ) if status == "completed" else f"Bulk duplicate scan FAILED: {error_detail}"

        return {
            "summary": summary,
            "candidates_scanned": stats['candidates_scanned'],
            "duplicates_found": stats['duplicates_found'],
            "merged": stats['merged'],
            "skipped_both_placements": stats['skipped_both_placements'],
            "skipped_below_threshold": stats['skipped_below_threshold'],
            "errors": stats['errors'],
        }

    def _send_bulk_scan_notification(self, stats, status, error_detail=None):
        try:
            from models import VettingConfig
            config = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
            admin_email = (config.setting_value.strip() if config and config.setting_value else '') or 'kroots@myticas.com'

            from email_service import EmailService
            email_svc = EmailService()

            if status == "completed":
                subject = "Scout Genius — Bulk Duplicate Scan Complete"
                message = (
                    f"The bulk duplicate candidate scan has completed successfully.\n\n"
                    f"Results Summary:\n"
                    f"  - Candidates scanned: {stats.get('candidates_scanned', 0):,}\n"
                    f"  - Duplicates found: {stats.get('duplicates_found', 0):,}\n"
                    f"  - Successfully merged: {stats.get('merged', 0):,}\n"
                    f"  - Skipped (below threshold): {stats.get('skipped_below_threshold', 0):,}\n"
                    f"  - Skipped (placement conflict): {stats.get('skipped_both_placements', 0):,}\n"
                    f"  - Errors: {stats.get('errors', 0):,}\n\n"
                    f"You can now resume the hourly Duplicate Candidate Merge scheduled job "
                    f"from the Automation Hub.\n\n"
                    f"— Scout Genius Automation"
                )
            else:
                subject = "Scout Genius — Bulk Duplicate Scan FAILED"
                message = (
                    f"The bulk duplicate candidate scan encountered a fatal error and could not complete.\n\n"
                    f"Error: {error_detail}\n\n"
                    f"Progress before failure:\n"
                    f"  - Candidates scanned: {stats.get('candidates_scanned', 0):,}\n"
                    f"  - Merged before failure: {stats.get('merged', 0):,}\n\n"
                    f"Please check the Automation Hub run history for details.\n\n"
                    f"— Scout Genius Automation"
                )

            email_svc.send_notification_email(admin_email, subject, message, 'bulk_duplicate_scan')
            logger.info(f"🔀 Bulk scan notification sent to {admin_email} (status={status})")
        except Exception as e:
            logger.warning(f"🔀 Failed to send bulk scan notification: {e}")

