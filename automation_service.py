import os
import json
import logging
import time
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

LONG_RUNNING_BUILTINS = {
    "update_field_bulk",
    "cleanup_ai_notes",
    "cleanup_duplicate_notes",
    "resume_reparser",
    "export_qualified",
    "email_extractor",
    "retry_recruiter_notifications",
}

logger = logging.getLogger(__name__)

class AutomationService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._bullhorn = None

    @property
    def bullhorn(self):
        if self._bullhorn is None:
            from bullhorn_service import BullhornService
            self._bullhorn = BullhornService()
        return self._bullhorn

    def get_all_logs(self, limit=50):
        return AutomationLog.query.order_by(
            AutomationLog.created_at.desc()
        ).limit(limit).all()

    def get_tasks(self, status=None):
        query = AutomationTask.query.order_by(AutomationTask.created_at.desc())
        if status:
            query = query.filter_by(status=status)
        return query.all()

    def get_task(self, task_id):
        return AutomationTask.query.get(task_id)

    def get_task_logs(self, task_id, limit=50):
        return AutomationLog.query.filter_by(
            automation_task_id=task_id
        ).order_by(AutomationLog.created_at.desc()).limit(limit).all()

    def delete_task(self, task_id):
        task = AutomationTask.query.get(task_id)
        if task:
            db.session.delete(task)
            db.session.commit()
            return True
        return False

    def update_task_status(self, task_id, status):
        task = AutomationTask.query.get(task_id)
        if task:
            task.status = status
            task.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        return False

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

    def _bh_headers(self):
        return {
            'BhRestToken': self.bullhorn.rest_token,
            'Content-Type': 'application/json'
        }

    def _bh_url(self):
        return self.bullhorn.base_url

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
            "retry_recruiter_notifications": self._builtin_retry_recruiter_notifications,
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

    AI_ACTION_PATTERNS = ["AI Vetting", "AI Resume Summary", "AI Vetted"]

    def _builtin_cleanup_ai_notes(self, params):
        dry_run = params.get("dry_run", True)
        candidate_ids = params.get("candidate_ids")
        max_candidates = params.get("max_candidates", 500)

        if not candidate_ids:
            candidate_ids = self._get_recent_candidates(max_candidates)

        ai_notes = []
        for i, cid in enumerate(candidate_ids):
            try:
                notes = self._get_candidate_entity_notes(cid)
                for note in notes:
                    action = note.get("action") or ""
                    if any(p in action for p in self.AI_ACTION_PATTERNS):
                        person = note.get("commentingPerson") or {}
                        ai_notes.append({
                            "note_id": note["id"],
                            "candidate_id": cid,
                            "action": action,
                            "comments_preview": (note.get("comments") or "")[:150]
                        })
            except Exception:
                pass
            if (i + 1) % 100 == 0:
                time.sleep(0.1)

        deleted = 0
        failed = 0
        if not dry_run:
            for note_info in ai_notes:
                try:
                    self._soft_delete_note(note_info["note_id"])
                    deleted += 1
                except Exception:
                    failed += 1

        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {len(ai_notes)} AI notes across {len(candidate_ids)} candidates scanned"
                       + (f". Deleted {deleted}, failed {failed}" if not dry_run else ""),
            "dry_run": dry_run,
            "candidates_scanned": len(candidate_ids),
            "ai_notes_found": len(ai_notes),
            "deleted": deleted,
            "failed": failed,
            "notes": ai_notes[:50]
        }

    def _builtin_cleanup_duplicate_notes(self, params):
        dry_run = params.get("dry_run", True)
        days_back = params.get("days_back", 5)
        max_candidates = params.get("max_candidates", 500)
        chain_window = 60

        candidate_ids = self._get_recent_candidates(max_candidates)
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        cutoff_ts = int(cutoff.timestamp() * 1000)

        duplicates = {}
        total_delete = 0

        for cid in candidate_ids:
            try:
                notes = self._get_candidate_entity_notes(cid)
                target_notes = [
                    n for n in notes
                    if (n.get("action") or "") == "AI Vetting - Not Recommended"
                    and (n.get("dateAdded") or 0) >= cutoff_ts
                ]
                if len(target_notes) < 2:
                    continue

                target_notes.sort(key=lambda n: n.get("dateAdded", 0))
                keep = [target_notes[0]]
                to_delete = []
                prev_time = target_notes[0].get("dateAdded", 0)

                for note in target_notes[1:]:
                    gap_ms = note.get("dateAdded", 0) - prev_time
                    gap_min = gap_ms / 60000
                    if gap_min <= chain_window:
                        to_delete.append({
                            "id": note["id"],
                            "gap_minutes": round(gap_min, 1)
                        })
                    else:
                        keep.append(note)
                    prev_time = note.get("dateAdded", 0)

                if to_delete:
                    duplicates[cid] = {"keep": len(keep), "delete": to_delete}
                    total_delete += len(to_delete)
            except Exception:
                pass

        deleted = 0
        failed = 0
        if not dry_run:
            for cid, data in duplicates.items():
                for note in data["delete"]:
                    try:
                        self._soft_delete_note(note["id"])
                        deleted += 1
                    except Exception:
                        failed += 1

        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {total_delete} duplicate notes across {len(duplicates)} candidates"
                       + (f". Deleted {deleted}, failed {failed}" if not dry_run else ""),
            "dry_run": dry_run,
            "candidates_scanned": len(candidate_ids),
            "candidates_with_duplicates": len(duplicates),
            "duplicate_notes_found": total_delete,
            "deleted": deleted,
            "failed": failed,
            "details": {str(k): {"keep": v["keep"], "delete_count": len(v["delete"])} for k, v in list(duplicates.items())[:20]}
        }

    def _builtin_find_zero_match(self, params):
        dry_run = params.get("dry_run", True)
        hours_back = params.get("hours_back", 6)
        do_delete = params.get("delete", False) and not dry_run

        cutoff_ts = int((datetime.utcnow() - timedelta(hours=hours_back)).timestamp() * 1000)
        url = f"{self._bh_url()}search/Note"
        all_notes = []
        start = 0

        while True:
            p = {
                "query": f"dateAdded:[{cutoff_ts} TO *]",
                "fields": "id,action,comments,dateAdded,personReference(id,firstName,lastName,email)",
                "count": 500,
                "start": start,
                "sort": "-dateAdded"
            }
            resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("data", [])
            total = data.get("total", 0)

            matching = [n for n in batch if (n.get("action") or "") == "AI Vetting - Not Recommended"]
            all_notes.extend(matching)
            start += len(batch)
            if len(batch) < 500 or start >= total:
                break

        zero_match = []
        for note in all_notes:
            comments = note.get("comments") or ""
            if "Highest Match Score: 0%" in comments:
                person = note.get("personReference") or {}
                zero_match.append({
                    "note_id": note["id"],
                    "candidate_id": person.get("id"),
                    "candidate_name": f"{person.get('firstName', '')} {person.get('lastName', '')}".strip(),
                    "email": person.get("email", ""),
                    "comments_preview": comments[:150]
                })

        deleted = 0
        if do_delete:
            for item in zero_match:
                try:
                    self._soft_delete_note(item["note_id"])
                    deleted += 1
                except Exception:
                    pass

        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {len(zero_match)} zero-match candidates in last {hours_back} hours"
                       + (f". Deleted {deleted} notes" if do_delete else ""),
            "dry_run": dry_run,
            "total_notes_scanned": len(all_notes),
            "zero_match_found": len(zero_match),
            "deleted": deleted,
            "candidates": zero_match[:50]
        }

    def _builtin_export_qualified(self, params):
        job_ids = params.get("job_ids", [])
        if not job_ids:
            return {"error": "job_ids parameter is required (list of job IDs)"}

        qualifying_actions = [
            "Scout Screen - Qualified",
            "AI Vetting - Qualified",
            "AI Vetting - Recommended",
            "AI Vetted - Accept",
        ]

        qualified = []
        all_actions_seen = set()

        for job_id in job_ids:
            subs = []
            start = 0
            while True:
                url = f"{self._bh_url()}search/JobSubmission"
                p = {
                    "query": f"jobOrder.id:{job_id} AND isDeleted:0",
                    "fields": "id,status,dateAdded,candidate(id,firstName,lastName,email,phone,occupation,source)",
                    "count": 500,
                    "start": start,
                    "sort": "-dateAdded"
                }
                resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                subs.extend(batch)
                if len(batch) < 500 or start + 500 >= data.get("total", 0):
                    break
                start += 500
                time.sleep(0.1)

            for sub in subs:
                cand = sub.get("candidate") or {}
                cid = cand.get("id")
                if not cid:
                    continue
                try:
                    notes = self._get_candidate_entity_notes(cid, count=200)
                    for note in notes:
                        action = note.get("action") or ""
                        if action:
                            all_actions_seen.add(action)
                        if action in qualifying_actions:
                            qualified.append({
                                "candidate_id": cid,
                                "first_name": cand.get("firstName", ""),
                                "last_name": cand.get("lastName", ""),
                                "email": cand.get("email", ""),
                                "phone": cand.get("phone", ""),
                                "source": cand.get("source", ""),
                                "occupation": cand.get("occupation", ""),
                                "job_id": job_id,
                                "note_action": action
                            })
                            break
                except Exception:
                    pass
                time.sleep(0.05)

        unique_ids = set(c["candidate_id"] for c in qualified)
        return {
            "summary": f"Found {len(qualified)} qualified candidates ({len(unique_ids)} unique) across {len(job_ids)} jobs",
            "total_rows": len(qualified),
            "unique_candidates": len(unique_ids),
            "by_job": {str(jid): len([c for c in qualified if c["job_id"] == jid]) for jid in job_ids},
            "all_actions_seen": sorted(all_actions_seen),
            "candidates": qualified
        }

    _GARBLED_PATTERNS = ["WW8Num", "OJQJ", "^J ", "phOJQJ", "OJQJo", "Num1z", "OJQJ^J"]

    def _is_garbled_description(self, text):
        if not text:
            return False
        matches = sum(1 for p in self._GARBLED_PATTERNS if p in text)
        return matches >= 3

    def _builtin_resume_reparser(self, params):
        dry_run = params.get("dry_run", True)
        days_back = params.get("days_back", 5)
        limit = params.get("limit", 100)
        fix_garbled = params.get("fix_garbled", False)
        candidate_ids = params.get("candidate_ids", [])

        cutoff_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
        search_url = f"{self._bh_url()}search/Candidate"
        candidates_to_process = []

        if candidate_ids:
            for cid in candidate_ids:
                try:
                    resp = requests.get(
                        f"{self._bh_url()}entity/Candidate/{cid}",
                        headers=self._bh_headers(),
                        params={"fields": "id,firstName,lastName,email,description"},
                        timeout=15
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data", {})
                    desc = data.get("description") or ""
                    reason = "garbled" if self._is_garbled_description(desc) else ("empty" if not desc.strip() else "force")
                    data["_reason"] = reason
                    candidates_to_process.append(data)
                except Exception as e:
                    self.logger.warning(f"resume_reparser: could not fetch candidate {cid}: {e}")
        else:
            try:
                resp = requests.get(search_url, headers=self._bh_headers(), params={
                    "query": f"dateAdded:[{cutoff_ts} TO *] AND -description:[* TO *]",
                    "fields": "id,firstName,lastName,email,description,dateAdded",
                    "count": limit,
                    "sort": "-dateAdded"
                }, timeout=30)
                resp.raise_for_status()
                for c in resp.json().get("data", []):
                    c["_reason"] = "empty"
                    candidates_to_process.append(c)
            except Exception as e:
                self.logger.warning(f"resume_reparser: empty-description search failed: {e}")

            if fix_garbled:
                try:
                    resp2 = requests.get(search_url, headers=self._bh_headers(), params={
                        "query": f"dateAdded:[{cutoff_ts} TO *] AND description:[* TO *]",
                        "fields": "id,firstName,lastName,email,description,dateAdded",
                        "count": max(limit, 200),
                        "sort": "-dateAdded"
                    }, timeout=30)
                    resp2.raise_for_status()
                    existing_ids = {c["id"] for c in candidates_to_process}
                    for c in resp2.json().get("data", []):
                        if c["id"] in existing_ids:
                            continue
                        if self._is_garbled_description(c.get("description") or ""):
                            c["_reason"] = "garbled"
                            candidates_to_process.append(c)
                except Exception as e:
                    self.logger.warning(f"resume_reparser: garbled-description search failed: {e}")

        garbled_found = sum(1 for c in candidates_to_process if c.get("_reason") == "garbled")
        results = {
            "candidates_found": len(candidates_to_process),
            "garbled_found": garbled_found,
            "with_resume": 0,
            "no_file": 0,
            "parsed": 0,
            "cleared": 0,
            "failed": 0,
        }
        candidate_details = []

        for cand in candidates_to_process:
            cid = cand.get("id")
            name = f"{cand.get('firstName', '')} {cand.get('lastName', '')}".strip()
            reason = cand.get("_reason", "empty")

            file_url = f"{self._bh_url()}entity/Candidate/{cid}/fileAttachments"
            try:
                file_resp = requests.get(file_url, headers=self._bh_headers(),
                                         params={"fields": "id,name,type,contentType"}, timeout=15)
                file_resp.raise_for_status()
                files = file_resp.json().get("data", [])
                resume_files = [f for f in files if
                                (f.get("type", "").lower() == "resume") or
                                f.get("name", "").lower().endswith((".pdf", ".doc", ".docx"))]
            except Exception:
                resume_files = []

            if not resume_files:
                results["no_file"] += 1
                if reason == "garbled" and not dry_run:
                    update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                    try:
                        requests.post(update_url,
                                      headers={**self._bh_headers(), "Content-Type": "application/json"},
                                      json={"description": ""}, timeout=15)
                        results["cleared"] += 1
                    except Exception as e:
                        self.logger.warning(f"resume_reparser: failed to clear garbled description for {cid}: {e}")
                continue

            results["with_resume"] += 1
            detail = {
                "candidate_id": cid,
                "name": name,
                "email": cand.get("email", ""),
                "resume_file": resume_files[0].get("name", "unknown"),
                "reason": reason,
                "status": "would_process" if dry_run else None
            }

            if not dry_run:
                try:
                    text = self._download_and_extract_text(cid, resume_files[0])
                    if text and len(text.strip()) > 50:
                        update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                        requests.post(update_url,
                                      headers={**self._bh_headers(), "Content-Type": "application/json"},
                                      json={"description": text[:20000]}, timeout=15)
                        results["parsed"] += 1
                        detail["status"] = "parsed"
                    elif reason == "garbled":
                        update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                        requests.post(update_url,
                                      headers={**self._bh_headers(), "Content-Type": "application/json"},
                                      json={"description": ""}, timeout=15)
                        results["cleared"] += 1
                        detail["status"] = "cleared_garbled"
                    else:
                        results["failed"] += 1
                        detail["status"] = "failed"
                except Exception as e:
                    self.logger.warning(f"resume_reparser: failed for candidate {cid}: {e}")
                    results["failed"] += 1
                    detail["status"] = "error"

            candidate_details.append(detail)

        mode_desc = "specific IDs" if candidate_ids else ("empty + garbled descriptions" if fix_garbled else "empty descriptions")
        summary_parts = [f"{'DRY RUN: ' if dry_run else ''}Scanned {results['candidates_found']} candidates ({mode_desc})"]
        if garbled_found:
            summary_parts.append(f"{garbled_found} garbled")
        summary_parts.append(f"{results['with_resume']} have resume files")
        if not dry_run:
            summary_parts.append(f"parsed {results['parsed']}, cleared {results['cleared']}, failed {results['failed']}")
        return {
            "summary": ", ".join(summary_parts),
            "dry_run": dry_run,
            "fix_garbled": fix_garbled,
            **results,
            "candidates": candidate_details[:50]
        }

    def _download_and_extract_text(self, candidate_id, resume_file_info):
        import base64
        import tempfile
        import os

        file_id = resume_file_info.get("id")
        filename = resume_file_info.get("name", "resume.pdf")

        dl_url = f"{self._bh_url()}file/Candidate/{candidate_id}/{file_id}"
        dl_resp = requests.get(dl_url, headers=self._bh_headers(), timeout=30)
        dl_resp.raise_for_status()
        file_data = dl_resp.json()
        file_content = file_data.get("File", {}).get("fileContent", "")

        if not file_content:
            return None

        raw_bytes = base64.b64decode(file_content)

        suffix = ""
        lower_name = filename.lower()
        if lower_name.endswith(".pdf"):
            suffix = ".pdf"
        elif lower_name.endswith(".docx"):
            suffix = ".docx"
        elif lower_name.endswith(".doc"):
            suffix = ".doc"
        else:
            suffix = ".pdf"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name

            from resume_parser import ResumeParser
            parser = ResumeParser()
            result = parser.parse_resume(tmp_path, quick_mode=True, skip_cache=True)

            formatted_html = result.get("formatted_html", "")
            if formatted_html and len(formatted_html.strip()) > 50:
                return formatted_html

            raw_text = result.get("raw_text", "")
            if not raw_text or len(raw_text.strip()) < 50:
                return raw_text

            return self._plain_text_to_html(raw_text)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _plain_text_to_html(self, text):
        import html as html_lib
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        parts = []
        for para in paragraphs:
            escaped = html_lib.escape(para)
            escaped = escaped.replace('\n', '<br>')
            parts.append(f"<p>{escaped}</p>")
        return "\n".join(parts)

    def _builtin_salesrep_sync(self, params):
        from salesrep_sync_service import run_salesrep_sync
        result = run_salesrep_sync(self.bullhorn)
        return {
            "summary": f"Sales Rep Sync: scanned {result.get('scanned', 0)}, "
                       f"updated {result.get('updated', 0)}, errors {result.get('errors', 0)}",
            **result
        }

    def _builtin_update_field_bulk(self, params):
        # THREAD-SAFETY RULE: This built-in runs in a background daemon thread.
        # All Bullhorn HTTP calls here MUST use standalone requests.get/post —
        # never bh.session.* — because requests.Session is shared with the main
        # Flask app and the APScheduler and is NOT thread-safe. Using the shared
        # session causes silent write failures (Bullhorn returns changeType:UPDATE
        # but data never persists). The other long-running built-ins already follow
        # this pattern. The Scout Automation Module must do the same.
        entity = params.get("entity", "Candidate")
        query = params.get("query", "").strip()
        updates = params.get("updates", {})
        batch_size = min(int(params.get("batch_size", 500)), 500)
        dry_run = params.get("dry_run", True)
        limit = params.get("limit")

        if not query:
            return {"error": "query parameter is required (Lucene search string)"}
        if not updates or not isinstance(updates, dict):
            return {"error": "updates parameter is required (dict of field→value pairs)"}

        bh = self.bullhorn
        search_url = f"{self._bh_url()}search/{entity}"

        sample_resp = requests.get(search_url, headers=self._bh_headers(), params={
            "query": query, "fields": "id", "count": 5, "start": 0,
        }, timeout=30)
        sample_data = sample_resp.json()
        total = sample_data.get("total", 0)
        sample_ids = [r["id"] for r in sample_data.get("data", [])]
        effective_total = min(total, int(limit)) if limit else total

        if dry_run:
            batches = (effective_total + batch_size - 1) // batch_size if effective_total else 0
            return {
                "summary": (
                    f"DRY RUN: Found {total:,} {entity} records matching '{query}'. "
                    + (f"Capped to {effective_total:,} by limit. " if limit else "")
                    + f"Would update {updates} across ~{batches:,} batch(es). "
                    f"Re-run with dry_run=false to execute."
                ),
                "dry_run": True,
                "entity": entity,
                "total_found": total,
                "effective_total": effective_total,
                "sample_ids": sample_ids,
                "fields_to_update": updates,
                "estimated_batches": batches,
            }

        succeeded = 0
        failed = 0
        failed_ids = []
        sample_updated_ids = []
        start = 0
        batch_number = 0
        first_batch_verified = False

        while start < effective_total:
            this_count = min(batch_size, effective_total - start)

            # Refresh Bullhorn auth token every 50 batches (~25k records, ~10 min) to prevent expiry
            if batch_number > 0 and batch_number % 50 == 0:
                try:
                    bh.authenticate()
                    self.logger.info(f"update_field_bulk: refreshed Bullhorn auth at batch {batch_number}")
                except Exception as auth_err:
                    self.logger.warning(f"update_field_bulk: auth refresh failed at batch {batch_number}: {auth_err}")

            resp = requests.get(search_url, headers=self._bh_headers(), params={
                "query": query, "fields": "id",
                "count": this_count, "start": start,
            }, timeout=30)
            batch_ids = [r["id"] for r in resp.json().get("data", [])]

            if not batch_ids:
                break

            for record_id in batch_ids:
                try:
                    upd = requests.post(
                        f"{self._bh_url()}entity/{entity}/{record_id}",
                        headers=self._bh_headers(),
                        json=updates, timeout=15
                    )
                    # Parse response body — Bullhorn returns HTTP 200 even for errors
                    try:
                        upd_body = upd.json()
                    except Exception:
                        upd_body = {}

                    bh_error = upd_body.get("errorCode") or upd_body.get("errors")
                    bh_confirmed = (
                        upd_body.get("changeType") == "UPDATE"
                        or upd_body.get("changedEntityId") is not None
                    )

                    if upd.status_code in (200, 201) and not bh_error and bh_confirmed:
                        succeeded += 1
                        if len(sample_updated_ids) < 5:
                            sample_updated_ids.append(record_id)

                        # Read-back spot-check after the very first confirmed update
                        if not first_batch_verified and succeeded == 1:
                            first_batch_verified = True
                            try:
                                check = requests.get(
                                    f"{self._bh_url()}entity/{entity}/{record_id}",
                                    headers=self._bh_headers(),
                                    params={"fields": ",".join(updates.keys())},
                                    timeout=15
                                )
                                check_data = check.json()
                                record_data = check_data.get("data", check_data)
                                mismatches = {
                                    field: {"expected": val, "actual": record_data.get(field)}
                                    for field, val in updates.items()
                                    if record_data.get(field) != val
                                }
                                if mismatches:
                                    return {
                                        "error": (
                                            f"Read-back verification FAILED after first update (ID {record_id}). "
                                            f"Changes did not persist in Bullhorn. Halting to prevent wasted API calls."
                                        ),
                                        "record_id": record_id,
                                        "mismatches": mismatches,
                                        "raw_readback": record_data,
                                        "succeeded_before_halt": succeeded,
                                    }
                                self.logger.info(
                                    f"update_field_bulk: read-back verified for ID {record_id} — changes confirmed"
                                )
                            except Exception as verify_err:
                                self.logger.warning(f"update_field_bulk: read-back check failed: {verify_err}")
                    else:
                        failed += 1
                        if len(failed_ids) < 10:
                            failed_ids.append({
                                "id": record_id,
                                "status": upd.status_code,
                                "bh_error": bh_error if bh_error else None,
                                "response": upd_body if upd_body else upd.text[:300]
                            })
                except Exception as e:
                    failed += 1
                    if len(failed_ids) < 10:
                        failed_ids.append({"id": record_id, "error": str(e)[:100]})

            start += len(batch_ids)
            batch_number += 1
            time.sleep(0.05)

        return {
            "summary": (
                f"Bulk update complete: {succeeded:,} {entity} records updated "
                f"({updates}), {failed:,} failed."
            ),
            "dry_run": False,
            "entity": entity,
            "total_processed": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "sample_updated_ids": sample_updated_ids,
            "failed_ids": failed_ids,
        }

    def _builtin_email_extractor(self, params):
        import re as _re

        dry_run = params.get("dry_run", True)
        days_back = params.get("days_back", 365)
        limit = params.get("limit", 50)

        EMAIL_RE = _re.compile(
            r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',
            _re.IGNORECASE
        )

        JUNK_DOMAINS = {
            "example.com", "test.com", "email.com", "youremail.com",
            "company.com", "domain.com", "placeholder.com",
        }

        def _is_valid_email(email_str):
            if not email_str or len(email_str) < 5 or len(email_str) > 254:
                return False
            local, _, domain = email_str.partition("@")
            if not domain or domain.lower() in JUNK_DOMAINS:
                return False
            if local.startswith(".") or local.endswith(".") or ".." in local:
                return False
            return True

        cutoff_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
        url = f"{self._bh_url()}search/Candidate"
        p = {
            "query": f"dateAdded:[{cutoff_ts} TO *] AND -email:[* TO *]",
            "fields": "id,firstName,lastName,email,dateAdded",
            "count": min(limit, 500),
            "sort": "-dateAdded"
        }
        resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        total_available = data.get("total", 0)
        candidates = data.get("data", [])

        also_empty = []
        if len(candidates) < limit:
            empty_url = f"{self._bh_url()}search/Candidate"
            empty_p = {
                "query": f'dateAdded:[{cutoff_ts} TO *] AND email:""',
                "fields": "id,firstName,lastName,email,dateAdded",
                "count": min(limit - len(candidates), 500),
                "sort": "-dateAdded"
            }
            try:
                empty_resp = requests.get(empty_url, headers=self._bh_headers(), params=empty_p, timeout=30)
                empty_resp.raise_for_status()
                empty_data = empty_resp.json()
                total_available += empty_data.get("total", 0)
                existing_ids = {c["id"] for c in candidates}
                also_empty = [c for c in empty_data.get("data", []) if c["id"] not in existing_ids]
                candidates.extend(also_empty[:limit - len(candidates)])
            except Exception:
                pass

        results = {
            "total_without_email": total_available,
            "candidates_in_batch": len(candidates),
            "with_resume": 0,
            "no_file": 0,
            "emails_found": 0,
            "emails_updated": 0,
            "no_email_in_resume": 0,
            "failed": 0,
        }
        candidate_details = []
        updated_samples = []

        for cand in candidates:
            cid = cand.get("id")
            name = f"{cand.get('firstName', '')} {cand.get('lastName', '')}".strip()

            file_url = f"{self._bh_url()}entity/Candidate/{cid}/fileAttachments"
            try:
                file_resp = requests.get(file_url, headers=self._bh_headers(),
                                         params={"fields": "id,name,type,contentType"}, timeout=15)
                file_resp.raise_for_status()
                files = file_resp.json().get("data", [])
                resume_files = [f for f in files if
                                (f.get("type", "").lower() == "resume") or
                                f.get("name", "").lower().endswith((".pdf", ".doc", ".docx"))]
            except Exception:
                resume_files = []

            if not resume_files:
                results["no_file"] += 1
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "status": "no_resume_file"
                })
                continue

            results["with_resume"] += 1
            resume_file = resume_files[0]

            if dry_run:
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "resume_file": resume_file.get("name", "unknown"),
                    "status": "would_process"
                })
                continue

            try:
                text = self._download_and_extract_text(cid, resume_file)
                if not text or len(text.strip()) < 10:
                    results["failed"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "status": "parse_failed"
                    })
                    continue

                emails_found = EMAIL_RE.findall(text)
                cleaned_emails = []
                for raw_email in emails_found:
                    cleaned = raw_email.strip()
                    cleaned = cleaned.lstrip("(<[")
                    cleaned = cleaned.rstrip(")>].,;:!?\"'")
                    if cleaned:
                        cleaned_emails.append(cleaned)
                valid_emails = [e.lower() for e in cleaned_emails if _is_valid_email(e)]

                seen = set()
                unique_emails = []
                for e in valid_emails:
                    if e not in seen:
                        seen.add(e)
                        unique_emails.append(e)

                if not unique_emails:
                    results["no_email_in_resume"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "resume_file": resume_file.get("name", ""),
                        "status": "no_email_in_resume"
                    })
                    continue

                results["emails_found"] += 1
                chosen_email = unique_emails[0]

                update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                upd_resp = requests.post(
                    update_url,
                    headers={**self._bh_headers(), "Content-Type": "application/json"},
                    json={"email": chosen_email},
                    timeout=15
                )
                upd_body = upd_resp.json() if upd_resp.status_code in (200, 201) else {}
                if upd_body.get("changeType") == "UPDATE" or upd_body.get("changedEntityId"):
                    results["emails_updated"] += 1
                    detail = {
                        "candidate_id": cid, "name": name,
                        "email_extracted": chosen_email,
                        "status": "updated"
                    }
                    if len(unique_emails) > 1:
                        detail["other_emails_found"] = unique_emails[1:4]
                    candidate_details.append(detail)
                    if len(updated_samples) < 10:
                        updated_samples.append({"id": cid, "name": name, "email": chosen_email})
                else:
                    results["failed"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "email_extracted": chosen_email,
                        "status": "update_failed",
                        "response": str(upd_body)[:200]
                    })
            except Exception as e:
                results["failed"] += 1
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "status": "error",
                    "error": str(e)[:150]
                })

            time.sleep(0.1)

        if dry_run:
            summary = (
                f"DRY RUN: {results['total_without_email']:,} candidates have no email address "
                f"(past {days_back} days). This batch covers {results['candidates_in_batch']}. "
                f"{results['with_resume']} have resume files, {results['no_file']} have no files attached. "
                f"Re-run with dry_run=false to extract and update emails."
            )
        else:
            summary = (
                f"Processed {results['candidates_in_batch']} candidates: "
                f"{results['emails_found']} emails extracted from resumes, "
                f"{results['emails_updated']} successfully updated in Bullhorn. "
                f"{results['no_email_in_resume']} resumes had no email, "
                f"{results['no_file']} had no resume file, "
                f"{results['failed']} failed."
            )

        return {
            "summary": summary,
            "dry_run": dry_run,
            **results,
            "updated_samples": updated_samples,
            "candidates": candidate_details[:50],
        }

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
