import os
import json
import logging
import time
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import anthropic
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Scout Genius Product Expert Assistant — an AI automation builder for Bullhorn ATS/CRM.

You help the product expert (your user) design, validate, and execute custom automations against the Bullhorn REST API. You have access to a live Bullhorn instance via the BullhornService.

AVAILABLE BULLHORN OPERATIONS (via execute_bullhorn_operation):
- search_candidates(email, phone, first_name, last_name) — Find candidates
- get_candidate(candidate_id) — Get candidate details
- create_candidate(data) — Create a new candidate
- update_candidate(candidate_id, data) — Update candidate fields
- create_candidate_note(candidate_id, note_text, action, user_id) — Add a note to a candidate
- get_candidate_notes(candidate_id, action_filter) — Get notes for a candidate, optionally filtered by action types
- get_job_order(job_id) — Get job order details
- get_job_orders() — List job orders
- get_jobs_by_query(query) — Search jobs with a query string
- get_tearsheets() — List all tearsheets
- get_tearsheet_jobs(tearsheet_id) — Get jobs in a tearsheet
- create_job_submission(candidate_id, job_id) — Submit candidate to job
- get_user_emails(user_ids) — Get user email addresses
- raw_api(method, endpoint, query_params, body) — Call any Bullhorn REST endpoint directly

BUILT-IN AUTOMATIONS (via run_builtin):
These are pre-built, tested automations that the user can trigger by name:

1. **cleanup_ai_notes** — Delete AI vetting notes from candidate records
   - Finds notes with actions matching: "AI Vetting", "AI Resume Summary", "AI Vetted"
   - Params: candidate_ids (optional list), max_candidates (default 500), dry_run (default true)
   - ALWAYS run with dry_run=true first and show results before executing

2. **cleanup_duplicate_notes** — Remove duplicate "AI Vetting - Not Recommended" notes
   - Finds candidates with multiple Not Recommended notes added within 60 minutes of each other
   - Keeps the original (oldest) note, deletes subsequent chained duplicates
   - Params: days_back (default 5), max_candidates (default 500), dry_run (default true)

3. **find_zero_match** — Find candidates with "Highest Match Score: 0%" in vetting notes
   - Searches recent notes for zero-score candidates that may need review
   - Params: hours_back (default 6), dry_run (default true), delete (default false)

4. **export_qualified** — Export qualified candidates for specific jobs
   - Fetches all submissions for given job IDs, checks each candidate's notes for qualifying actions
   - Qualifying actions: "Scout Screen - Qualified", "AI Vetting - Qualified", "AI Vetting - Recommended", "AI Vetted - Accept"
   - Params: job_ids (required list of job IDs)
   - Returns CSV-ready data with candidate details

5. **resume_reparser** — Find and re-parse candidates with missing resume text
   - Finds recently added candidates with empty description but attached resume files
   - Downloads resume, tries Bullhorn parser first, then falls back to OpenAI Vision for image PDFs
   - Params: days_back (default 5), limit (default 100), dry_run (default true)

6. **salesrep_sync** — Manually trigger Sales Rep display name sync
   - Resolves CorporateUser IDs in customText3 to display names in customText6
   - Normally runs automatically every 30 minutes; use this for an immediate sync
   - No params needed

BULLHORN API NOTES:
- Note soft-delete uses POST to entity/Note/{id} with {"isDeleted": true}
- search/Note uses Lucene queries; action values may not be indexed — use entity/Candidate/{id}/notes for reliable note retrieval
- Candidate notes via entity endpoint: GET entity/Candidate/{id}/notes with fields and count params
- For bulk operations, paginate using start/count params (max 500 per page)
- Timestamps in Bullhorn are milliseconds since epoch

AUTOMATION WORKFLOW:
1. User describes what they want to automate in plain language
2. You interpret the request and determine if a built-in automation fits or if custom API calls are needed
3. You propose a plan with clear steps
4. You ask for confirmation before executing
5. On confirmation, you execute and report results
6. If it's a built-in automation, ALWAYS dry-run first

SAFETY RULES:
- ALWAYS propose a dry-run or preview first before making changes
- ALWAYS ask for explicit confirmation before executing write operations (create, update, delete)
- When doing bulk operations, start with a small sample (5-10 records) to validate
- Log all operations for audit trail
- If unsure about the scope of an operation, ask clarifying questions

RESPONSE FORMAT:
When proposing an automation, structure your response as:
1. **Understanding**: Restate what the user wants
2. **Plan**: Step-by-step breakdown of what you'll do
3. **Preview**: Show sample data or expected results if possible
4. **Confirmation**: Ask if they want to proceed

When you have enough information to create a named automation task, include this JSON block:
```json
{"automation": {"name": "Short task name", "description": "What this automation does", "type": "one-time|scheduled|query"}}
```

When you want to execute a built-in automation, include this JSON block:
```json
{"run_builtin": {"name": "cleanup_ai_notes", "params": {"dry_run": true}}}
```

Keep responses clear and conversational. The user is a product expert, not a developer."""


class AutomationService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if api_key:
            self.anthropic_client = anthropic.Anthropic(api_key=api_key)
            self.logger.info("AutomationService: Anthropic client initialized (Claude Opus 4)")
        else:
            self.anthropic_client = None
            self.logger.warning("AutomationService: ANTHROPIC_API_KEY not found")
        
        self._bullhorn = None

    @property
    def bullhorn(self):
        if self._bullhorn is None:
            from bullhorn_service import BullhornService
            self._bullhorn = BullhornService()
        return self._bullhorn

    def chat(self, user_message, task_id=None):
        if not self.anthropic_client:
            return {"error": "Anthropic API key not configured"}

        chat_history = []
        if task_id:
            existing = AutomationChat.query.filter_by(
                automation_task_id=task_id
            ).order_by(AutomationChat.created_at).all()
            for msg in existing:
                chat_history.append({"role": msg.role, "content": msg.content})
        else:
            existing = AutomationChat.query.filter_by(
                automation_task_id=None
            ).order_by(AutomationChat.created_at).all()
            for msg in existing:
                chat_history.append({"role": msg.role, "content": msg.content})

        user_chat = AutomationChat(
            automation_task_id=task_id,
            role='user',
            content=user_message
        )
        db.session.add(user_chat)
        db.session.commit()

        messages = list(chat_history)
        messages.append({"role": "user", "content": user_message})

        try:
            response = self.anthropic_client.messages.create(
                model="claude-opus-4-20250514",
                system=SYSTEM_PROMPT,
                messages=messages,
                temperature=0.3,
                max_tokens=4096
            )
            
            assistant_content = response.content[0].text

            assistant_chat = AutomationChat(
                automation_task_id=task_id,
                role='assistant',
                content=assistant_content
            )
            db.session.add(assistant_chat)
            db.session.commit()

            task_created = None
            builtin_result = None

            if '```json' in assistant_content:
                try:
                    json_start = assistant_content.index('```json') + 7
                    json_end = assistant_content.index('```', json_start)
                    json_str = assistant_content[json_start:json_end].strip()
                    parsed = json.loads(json_str)

                    if 'automation' in parsed:
                        auto_def = parsed['automation']
                        task = AutomationTask(
                            name=auto_def.get('name', 'Untitled Automation'),
                            description=auto_def.get('description', ''),
                            automation_type=auto_def.get('type', 'one-time'),
                            status='draft'
                        )
                        db.session.add(task)
                        db.session.commit()
                        task_created = {
                            'id': task.id,
                            'name': task.name,
                            'description': task.description,
                            'type': task.automation_type
                        }

                        if task_id is None:
                            unlinked = AutomationChat.query.filter_by(
                                automation_task_id=None
                            ).all()
                            for chat in unlinked:
                                chat.automation_task_id = task.id
                            db.session.commit()

                        self.logger.info(f"Created automation task: {task.name} (ID: {task.id})")

                    if 'run_builtin' in parsed:
                        builtin_def = parsed['run_builtin']
                        builtin_name = builtin_def.get('name')
                        builtin_params = builtin_def.get('params', {})
                        if builtin_name:
                            builtin_result = self.run_builtin(
                                builtin_name, builtin_params,
                                task_id=task_id or (task_created['id'] if task_created else None)
                            )
                            self.logger.info(f"Auto-executed built-in: {builtin_name}")
                except (json.JSONDecodeError, ValueError):
                    pass

            return {
                "response": assistant_content,
                "task_created": task_created,
                "task_id": task_id,
                "builtin_result": builtin_result
            }

        except Exception as e:
            self.logger.error(f"AutomationService chat error: {e}")
            return {"error": str(e)}

    def execute_bullhorn_operation(self, operation, params, task_id=None):
        try:
            bh = self.bullhorn
            result = None

            if operation == 'search_candidates':
                result = bh.search_candidates(**params)
            elif operation == 'get_candidate':
                result = bh.get_candidate(params.get('candidate_id'))
            elif operation == 'get_job_order':
                result = bh.get_job_order(params.get('job_id'))
            elif operation == 'get_job_orders':
                result = bh.get_job_orders()
            elif operation == 'get_jobs_by_query':
                result = bh.get_jobs_by_query(params.get('query', ''))
            elif operation == 'get_tearsheets':
                result = bh.get_tearsheets()
            elif operation == 'get_tearsheet_jobs':
                result = bh.get_tearsheet_jobs(params.get('tearsheet_id'))
            elif operation == 'get_candidate_notes':
                result = bh.get_candidate_notes(params.get('candidate_id'))
            elif operation == 'create_candidate_note':
                result = bh.create_candidate_note(
                    params.get('candidate_id'),
                    params.get('note_text'),
                    action=params.get('action', 'Automation Note')
                )
            elif operation == 'update_candidate':
                result = bh.update_candidate(
                    params.get('candidate_id'),
                    params.get('data', {})
                )
            elif operation == 'create_job_submission':
                result = bh.create_job_submission(
                    params.get('candidate_id'),
                    params.get('job_id')
                )
            elif operation == 'raw_api':
                result = bh._make_request(
                    params.get('method', 'GET'),
                    params.get('endpoint'),
                    params=params.get('query_params'),
                    data=params.get('body')
                )
            else:
                return {"error": f"Unknown operation: {operation}"}

            if task_id:
                log = AutomationLog(
                    automation_task_id=task_id,
                    status='success',
                    message=f"Executed: {operation}",
                    details_json=json.dumps({
                        'operation': operation,
                        'params': {k: str(v)[:200] for k, v in params.items()},
                        'result_preview': str(result)[:500] if result else None
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
            self.logger.error(f"Bullhorn operation error: {e}")
            if task_id:
                log = AutomationLog(
                    automation_task_id=task_id,
                    status='error',
                    message=f"Failed: {operation} - {str(e)}",
                    details_json=json.dumps({
                        'operation': operation,
                        'error': str(e)
                    })
                )
                db.session.add(log)
                db.session.commit()
            return {"error": str(e)}

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

    def get_chat_history(self, task_id=None):
        query = AutomationChat.query.filter_by(automation_task_id=task_id)
        return query.order_by(AutomationChat.created_at).all()

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

    def clear_chat_history(self, task_id=None):
        AutomationChat.query.filter_by(automation_task_id=task_id).delete()
        db.session.commit()

    def _bh_headers(self):
        return self.bullhorn._get_headers()

    def _bh_url(self):
        return self.bullhorn.rest_url

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

    def _builtin_resume_reparser(self, params):
        dry_run = params.get("dry_run", True)
        days_back = params.get("days_back", 5)
        limit = params.get("limit", 100)

        cutoff_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
        url = f"{self._bh_url()}search/Candidate"
        p = {
            "query": f"dateAdded:[{cutoff_ts} TO *] AND -description:[* TO *]",
            "fields": "id,firstName,lastName,email,description,dateAdded",
            "count": limit,
            "sort": "-dateAdded"
        }
        resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
        resp.raise_for_status()
        candidates = resp.json().get("data", [])

        results = {"candidates_found": len(candidates), "with_resume": 0, "no_file": 0, "parsed": 0, "failed": 0}
        candidate_details = []

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
                continue

            results["with_resume"] += 1
            candidate_details.append({
                "candidate_id": cid,
                "name": name,
                "email": cand.get("email", ""),
                "resume_file": resume_files[0].get("name", "unknown")
            })

            if not dry_run:
                try:
                    file_id = resume_files[0].get("id")
                    dl_url = f"{self._bh_url()}file/Candidate/{cid}/{file_id}"
                    dl_resp = requests.get(dl_url, headers=self._bh_headers(), timeout=30)
                    dl_resp.raise_for_status()
                    file_data = dl_resp.json()
                    file_content = file_data.get("File", {}).get("fileContent", "")

                    if file_content:
                        import base64
                        raw_bytes = base64.b64decode(file_content)
                        text = raw_bytes.decode("utf-8", errors="ignore")[:5000]
                        if len(text.strip()) > 50:
                            update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                            requests.post(update_url,
                                          headers={**self._bh_headers(), "Content-Type": "application/json"},
                                          json={"description": text}, timeout=15)
                            results["parsed"] += 1
                        else:
                            results["failed"] += 1
                    else:
                        results["failed"] += 1
                except Exception:
                    results["failed"] += 1

        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {results['candidates_found']} candidates with empty description, "
                       f"{results['with_resume']} have resume files"
                       + (f", parsed {results['parsed']}, failed {results['failed']}" if not dry_run else ""),
            "dry_run": dry_run,
            **results,
            "candidates": candidate_details[:50]
        }

    def _builtin_salesrep_sync(self, params):
        from salesrep_sync_service import run_salesrep_sync
        result = run_salesrep_sync(self.bullhorn)
        return {
            "summary": f"Sales Rep Sync: scanned {result.get('scanned', 0)}, "
                       f"updated {result.get('updated', 0)}, errors {result.get('errors', 0)}",
            **result
        }
