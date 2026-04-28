"""Dispatch entry points (`run_builtin`, `run_builtin_background`) and Bullhorn helpers shared by multiple builtins.

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

from automation_service.constants import (
    LONG_RUNNING_BUILTINS,
    NO_BULLHORN_BUILTINS,
)

logger = logging.getLogger(__name__)


class DispatchMixin:
    def run_builtin_background(self, name, params, task_id=None):
        if name in LONG_RUNNING_BUILTINS:
            service_ref = self

            def _run_bg(svc, bg_name, bg_params, bg_task_id):
                from app import app as flask_app
                with flask_app.app_context():
                    try:
                        svc.run_builtin(bg_name, bg_params, task_id=bg_task_id)
                    except Exception as e:
                        svc.logger.error(f"Background builtin {bg_name} error: {e}")

            bg_thread = threading.Thread(
                target=_run_bg,
                args=(service_ref, name, params, task_id),
                daemon=True
            )
            bg_thread.start()
            self.logger.info(f"Launched background built-in: {name}")
            return {"status": "background", "name": name, "task_id": task_id}
        else:
            return self.run_builtin(name, params, task_id=task_id)

    def _get_recent_candidates(self, max_count=500):
        url = f"{self._bh_url()}search/Candidate"
        params = {
            "query": "id:[1 TO *]",
            "fields": "id",
            "count": max_count,
            "sort": "-dateLastModified"
        }
        resp = requests.get(url, headers=self._bh_headers(), params=params, timeout=30)
        resp.raise_for_status()
        return [c.get("id") for c in resp.json().get("data", [])]

    def _get_candidate_entity_notes(self, candidate_id, count=100):
        url = f"{self._bh_url()}entity/Candidate/{candidate_id}/notes"
        params = {
            "fields": "id,action,comments,dateAdded,commentingPerson(id,firstName,lastName)",
            "count": count
        }
        resp = requests.get(url, headers=self._bh_headers(), params=params, timeout=20)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def _soft_delete_note(self, note_id):
        url = f"{self._bh_url()}entity/Note/{note_id}"
        resp = requests.post(url, headers={**self._bh_headers(), "Content-Type": "application/json"},
                             json={"isDeleted": True}, timeout=15)
        resp.raise_for_status()
        return True

    def run_builtin(self, name, params=None, task_id=None):
        params = params or {}
        if name not in NO_BULLHORN_BUILTINS:
            try:
                self.bullhorn.authenticate()
            except Exception as e:
                return {"error": f"Bullhorn auth failed: {e}"}

        handlers = {
            "cleanup_ai_notes": self._builtin_cleanup_ai_notes,
            "cleanup_duplicate_notes": self._builtin_cleanup_duplicate_notes,
            "find_zero_match": self._builtin_find_zero_match,
            "export_qualified": self._builtin_export_qualified,
            "resume_reparser": self._builtin_resume_reparser,
            "salesrep_sync": self._builtin_salesrep_sync,
            "update_field_bulk": self._builtin_update_field_bulk,
            "email_extractor": self._builtin_email_extractor,
            "occupation_extractor": self._builtin_occupation_extractor,
            "retry_recruiter_notifications": self._builtin_retry_recruiter_notifications,
            "screening_audit": self._builtin_screening_audit,
            "duplicate_merge_scan": self._builtin_duplicate_merge_scan,
        }

        handler = handlers.get(name)
        if not handler:
            return {"error": f"Unknown built-in automation: {name}"}

        try:
            result = handler(params)

            if task_id:
                log = AutomationLog(
                    automation_task_id=task_id,
                    status='success',
                    message=f"Built-in: {name}",
                    details_json=json.dumps({
                        "builtin": name,
                        "params": {k: str(v)[:200] for k, v in params.items()},
                        "summary": str(result.get("summary", ""))[:500]
                    })
                )
                db.session.add(log)
                task = AutomationTask.query.get(task_id)
                if task:
                    task.last_run_at = datetime.utcnow()
                    task.run_count += 1
                db.session.commit()

            return {"success": True, "result": result}

        except Exception as e:
            self.logger.error(f"Built-in automation {name} error: {e}")
            if task_id:
                log = AutomationLog(
                    automation_task_id=task_id,
                    status='error',
                    message=f"Built-in {name} failed: {str(e)}",
                    details_json=json.dumps({"builtin": name, "error": str(e)})
                )
                db.session.add(log)
                db.session.commit()
            return {"error": str(e)}
