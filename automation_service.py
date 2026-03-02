import os
import json
import logging
import time
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import anthropic
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

LONG_RUNNING_BUILTINS = {
    "update_field_bulk",
    "cleanup_ai_notes",
    "cleanup_duplicate_notes",
    "resume_reparser",
    "export_qualified",
}

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Scout Genius Product Expert Assistant — an AI automation builder for Bullhorn ATS/CRM.

You help the product expert (your user) design, validate, and execute custom automations against the Bullhorn REST API. You have access to a live Bullhorn instance via the BullhornService.

HOW TO QUERY AND MODIFY BULLHORN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
To perform any Bullhorn operation, include an execute_operation JSON block in your response. The backend executes it immediately against the live Bullhorn API and injects the real result into the chat history. You will see it on your NEXT turn and can then report it accurately.

CRITICAL: After including an execute_operation block, STOP. Do not write the results you expect — you do not know them yet. Any result, count, ID, or field value you report to the user MUST come from a [Live Bullhorn Result] section that appears in this chat history.

ONLY ONE execute_operation block per response. If multiple calls are needed, do them one at a time across turns.

AVAILABLE OPERATIONS — include exactly one of these JSON block formats:

To search candidates:
```json
{"execute_operation": {"operation": "raw_api", "params": {"method": "GET", "endpoint": "search/Candidate", "query_params": {"fields": "id,firstName,lastName,source,email", "query": "source:LinkedIn", "count": 20, "start": 0}}}}
```

To get a single candidate:
```json
{"execute_operation": {"operation": "get_candidate", "params": {"candidate_id": 12345}}}
```

To update a candidate field:
```json
{"execute_operation": {"operation": "update_candidate", "params": {"candidate_id": 12345, "data": {"source": "LinkedIn Job Board"}}}}
```

To add a note to a candidate:
```json
{"execute_operation": {"operation": "create_candidate_note", "params": {"candidate_id": 12345, "note_text": "Note text here", "action": "Automation Note"}}}
```

To get notes for a candidate:
```json
{"execute_operation": {"operation": "get_candidate_notes", "params": {"candidate_id": 12345}}}
```

To call any Bullhorn REST endpoint directly (most flexible):
```json
{"execute_operation": {"operation": "raw_api", "params": {"method": "GET", "endpoint": "search/Candidate", "query_params": {"fields": "id,source", "query": "source:LinkedIn", "count": 500}}}}
```

For POST/PUT via raw_api, add a "body" key to params with the payload dict.

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

7. **update_field_bulk** — Bulk-update a field on any Bullhorn entity matching a search query
   - Searches for entities matching a Lucene query and applies specified field updates to all matches
   - Params: entity (default "Candidate"), query (Lucene query string, required), updates (dict of field→value, required), batch_size (default 500), dry_run (default true), limit (optional cap on records processed for safety)
   - ALWAYS run with dry_run=true first — it returns the real total count and 5 sample IDs so the user can verify scope before committing
   - Use this for bulk field changes: source tag normalisation, status updates, custom field resets, etc.
   - Example: `{"run_builtin": {"name": "update_field_bulk", "params": {"query": "source:LinkedIn", "updates": {"source": "LinkedIn Job Board"}, "dry_run": true}}}`

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

Keep responses clear and conversational. The user is a product expert, not a developer.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATA INTEGRITY — ABSOLUTE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. NEVER fabricate data. Do not invent candidate names, IDs, record counts, field values, or API responses. Every number, name, and ID you report must come from an actual API call made in this session. If you did not execute a query, you do not have results to report.

2. NEVER present code as executed unless it has actually run. Showing a code block is planning, not execution. If you cannot execute an operation, say so explicitly — do not show the output you expect.

3. ALWAYS verify after every write. After any update, create, or delete operation, immediately read the record back and confirm the change persisted. If the read-back shows the value did not change, stop and report the failure — do not continue as if the operation succeeded.

4. If a query returns no results or an operation fails, say so plainly. Do not speculate about what the results "would" be.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK ANCHORING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use the "Working on: ... [task description]." anchor ONLY in these situations:
1. Your very first response when a new task begins.
2. When the user changes direction — acknowledge the shift and restate the new task.
3. When resuming after a session break or a long gap.

Do NOT include it in follow-up responses during an ongoing task. Once a task is underway, respond directly without prefixing every reply with the anchor — it becomes noise.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLANNING vs. EXECUTION — KEEP THESE SEPARATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLANNING MODE: When proposing what you will do, be explicit that no action has been taken yet. Use language like "Here is what I will do — shall I proceed?" Never describe future actions in past tense.

EXECUTION MODE: Once the user confirms, execute the operations. After each operation, report what actually happened using past tense and real data from the response. Never show the same code block twice without executing it in between. If you are blocked from executing, say why instead of repeating the plan.

COMPLETION SUMMARY: After a task finishes, provide a factual summary: what was done, how many records were affected, and 2–5 real record IDs the user can verify manually. If zero records were affected, explain why.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NO CLIFFHANGERS — NEVER LEAVE THE USER WAITING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEVER end a response with a promise of an action that has not yet happened. Phrases like "Let me start by checking...", "I'll now query...", or "Let me search for..." must NEVER appear at the end of a response — they leave the user waiting for an update that never arrives.

If you intend to take an action, take it and report the result in the SAME response. If you cannot execute immediately (e.g., you need the user's confirmation first), say so explicitly and stop — do not hint at pending work.

Acceptable: "I checked X — here is what I found: [result]."
Acceptable: "Here is my plan. Shall I proceed?"
NOT acceptable: "Let me now check X." ← (and then the response ends)"""


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

        is_first_message = len(chat_history) == 0
        connection_info = None

        if is_first_message:
            connection_info = self._verify_connection()
            if not connection_info["connected"]:
                return {
                    "error": (
                        f"Cannot start session — Bullhorn connection failed: "
                        f"{connection_info.get('error', 'Unknown error')}. "
                        f"Please check the ATS integration settings and try again."
                    )
                }

        user_chat = AutomationChat(
            automation_task_id=task_id,
            role='user',
            content=user_message
        )
        db.session.add(user_chat)
        db.session.commit()

        messages = list(chat_history)
        messages.append({"role": "user", "content": user_message})

        system_prompt = SYSTEM_PROMPT
        if connection_info and connection_info["connected"]:
            system_prompt += (
                f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"ACTIVE BULLHORN CONNECTION (verified at session start)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Corporation: {connection_info['corporation']}\n"
                f"REST URL: {connection_info['rest_url']}\n"
                f"Status: Connected and authenticated\n\n"
                f"All operations in this session run against this specific instance. "
                f"Do not assume you are connected to any other environment."
            )

        try:
            response = self.anthropic_client.messages.create(
                model="claude-opus-4-20250514",
                system=system_prompt,
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
            operation_result = None

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
                            effective_builtin_task_id = task_id or (task_created['id'] if task_created else None)

                            if builtin_name in LONG_RUNNING_BUILTINS:
                                pending_section = (
                                    f"\n\n---\n**[Built-in Running: {builtin_name} — processing in background]**\n"
                                    f"The operation has started on the server. This message will be updated with results when complete — "
                                    f"you can safely close your laptop or this tab while it runs.\n---"
                                )
                                assistant_content += pending_section
                                assistant_chat.content = assistant_content
                                db.session.commit()

                                captured_chat_id = assistant_chat.id
                                service_ref = self
                                captured_builtin_name = builtin_name
                                captured_builtin_params = builtin_params
                                captured_task_id = effective_builtin_task_id

                                def _run_in_background(chat_id, svc, name, params, bg_task_id):
                                    from app import app as flask_app
                                    with flask_app.app_context():
                                        try:
                                            bg_result = svc.run_builtin(name, params, task_id=bg_task_id)
                                            result_json = json.dumps(
                                                bg_result.get('result', bg_result) if isinstance(bg_result, dict) else bg_result,
                                                indent=2, default=str
                                            )
                                            result_section = (
                                                f"\n\n---\n**[Built-in Result — {name}]**\n"
                                                f"```json\n{result_json}\n```\n---"
                                            )
                                            pending_marker = (
                                                f"\n\n---\n**[Built-in Running: {name} — processing in background]**\n"
                                                f"The operation has started on the server. This message will be updated with results when complete — "
                                                f"you can safely close your laptop or this tab while it runs.\n---"
                                            )
                                            chat_record = AutomationChat.query.get(chat_id)
                                            if chat_record:
                                                chat_record.content = chat_record.content.replace(
                                                    pending_marker, result_section
                                                )
                                                db.session.commit()
                                        except Exception as bg_err:
                                            svc.logger.error(f"Background builtin {name} error: {bg_err}")
                                            try:
                                                chat_record = AutomationChat.query.get(chat_id)
                                                if chat_record:
                                                    error_section = (
                                                        f"\n\n---\n**[Built-in Error — {name}]**\n"
                                                        f"```json\n{json.dumps({'error': str(bg_err)}, indent=2)}\n```\n---"
                                                    )
                                                    pending_marker = (
                                                        f"\n\n---\n**[Built-in Running: {name} — processing in background]**\n"
                                                        f"The operation has started on the server. This message will be updated with results when complete — "
                                                        f"you can safely close your laptop or this tab while it runs.\n---"
                                                    )
                                                    chat_record.content = chat_record.content.replace(
                                                        pending_marker, error_section
                                                    )
                                                    db.session.commit()
                                            except Exception:
                                                pass

                                bg_thread = threading.Thread(
                                    target=_run_in_background,
                                    args=(captured_chat_id, service_ref, captured_builtin_name,
                                          captured_builtin_params, captured_task_id),
                                    daemon=True
                                )
                                bg_thread.start()
                                builtin_result = {"status": "background", "name": builtin_name}
                                self.logger.info(f"Launched background built-in: {builtin_name}")

                            else:
                                builtin_result = self.run_builtin(
                                    builtin_name, builtin_params,
                                    task_id=effective_builtin_task_id
                                )
                                self.logger.info(f"Auto-executed built-in: {builtin_name}")
                                sync_result = builtin_result.get('result', builtin_result) if isinstance(builtin_result, dict) else builtin_result
                                result_json = json.dumps(sync_result, indent=2, default=str)
                                result_section = (
                                    f"\n\n---\n**[Built-in Result — {builtin_name}]**\n"
                                    f"```json\n{result_json}\n```\n---"
                                )
                                assistant_content += result_section
                                assistant_chat.content = assistant_content
                                db.session.commit()

                    if 'execute_operation' in parsed:
                        op_def = parsed['execute_operation']
                        op_name = op_def.get('operation')
                        op_params = op_def.get('params', {})
                        if op_name:
                            effective_task_id = task_id or (task_created['id'] if task_created else None)
                            operation_result = self.execute_bullhorn_operation(
                                op_name, op_params, task_id=effective_task_id
                            )
                            self.logger.info(f"Executed operation: {op_name}")

                            result_label = f"Live Bullhorn Result — {op_name}"
                            result_json = json.dumps(operation_result, indent=2, default=str)
                            result_section = (
                                f"\n\n---\n**[{result_label}]**\n"
                                f"```json\n{result_json}\n```\n---"
                            )
                            assistant_content += result_section
                            assistant_chat.content = assistant_content
                            db.session.commit()

                except (json.JSONDecodeError, ValueError):
                    pass

            return {
                "response": assistant_content,
                "task_created": task_created,
                "task_id": task_id,
                "builtin_result": builtin_result,
                "operation_result": operation_result
            }

        except Exception as e:
            self.logger.error(f"AutomationService chat error: {e}")
            return {"error": str(e)}

    def execute_bullhorn_operation(self, operation, params, task_id=None):
        try:
            bh = self.bullhorn
            try:
                bh.authenticate()
            except Exception as auth_err:
                return {"error": f"Bullhorn authentication failed: {auth_err}"}
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
                method = params.get('method', 'GET').upper()
                endpoint = params.get('endpoint', '').lstrip('/')
                url = f"{bh.base_url}{endpoint}"
                query_params = params.get('query_params') or {}
                query_params['BhRestToken'] = bh.rest_token
                body = params.get('body')
                if method == 'GET':
                    resp = bh.session.get(url, params=query_params, timeout=30)
                elif method == 'POST':
                    resp = bh.session.post(url, params=query_params, json=body, timeout=30)
                elif method == 'PUT':
                    resp = bh.session.put(url, params=query_params, json=body, timeout=30)
                elif method == 'DELETE':
                    resp = bh.session.delete(url, params=query_params, timeout=30)
                else:
                    return {"error": f"Unsupported HTTP method: {method}"}
                try:
                    result = resp.json()
                except Exception:
                    result = {"status_code": resp.status_code, "text": resp.text[:500]}
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

    def _verify_connection(self):
        """Verify Bullhorn connection and return connection context string."""
        try:
            self.bullhorn.authenticate()
            rest_url = self.bullhorn.base_url or "unknown"
            corp_name = "Unknown"
            try:
                resp = self.bullhorn._make_request('GET', 'settings/corporationName', params={})
                if resp and isinstance(resp, dict):
                    corp_name = resp.get('corporationName') or resp.get('name') or "Unknown"
            except Exception:
                pass
            return {
                "connected": True,
                "corporation": corp_name,
                "rest_url": rest_url
            }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e)
            }

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
