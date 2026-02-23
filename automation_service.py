import os
import json
import logging
from datetime import datetime
from openai import OpenAI
from app import db
from models import AutomationTask, AutomationLog, AutomationChat

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Scout Genius Product Expert Assistant — an AI automation builder for Bullhorn ATS/CRM.

You help the product expert (your user) design, validate, and execute custom automations against the Bullhorn REST API. You have access to a live Bullhorn instance via the BullhornService.

AVAILABLE BULLHORN OPERATIONS:
- search_candidates(email, phone, first_name, last_name) — Find candidates
- get_candidate(candidate_id) — Get candidate details
- create_candidate(data) — Create a new candidate
- update_candidate(candidate_id, data) — Update candidate fields
- create_candidate_note(candidate_id, note_text, action, user_id) — Add a note to a candidate
- get_candidate_notes(candidate_id) — Get notes for a candidate
- get_job_order(job_id) — Get job order details
- get_job_orders() — List job orders
- get_jobs_by_query(query) — Search jobs with a query string
- get_tearsheets() — List all tearsheets
- get_tearsheet_jobs(tearsheet_id) — Get jobs in a tearsheet
- create_job_submission(candidate_id, job_id) — Submit candidate to job
- get_user_emails(user_ids) — Get user email addresses

ALSO AVAILABLE (via direct Bullhorn REST API):
- Any Bullhorn REST API endpoint can be called via the generic _make_request(method, endpoint, params, data) method

AUTOMATION WORKFLOW:
1. User describes what they want to automate in plain language
2. You interpret the request and break it down into specific Bullhorn API operations
3. You propose a plan with clear steps
4. You ask for confirmation before executing
5. On confirmation, you execute and report results
6. If the user wants to schedule it, you help define the schedule

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

Keep responses clear and conversational. The user is a product expert, not a developer."""


class AutomationService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
            self.logger.info("AutomationService: OpenAI client initialized")
        else:
            self.openai_client = None
            self.logger.warning("AutomationService: OPENAI_API_KEY not found")
        
        self._bullhorn = None

    @property
    def bullhorn(self):
        if self._bullhorn is None:
            from bullhorn_service import BullhornService
            self._bullhorn = BullhornService()
        return self._bullhorn

    def chat(self, user_message, task_id=None):
        if not self.openai_client:
            return {"error": "OpenAI API key not configured"}

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

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(chat_history)
        messages.append({"role": "user", "content": user_message})

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.3,
                max_tokens=4096
            )
            
            assistant_content = response.choices[0].message.content

            assistant_chat = AutomationChat(
                automation_task_id=task_id,
                role='assistant',
                content=assistant_content
            )
            db.session.add(assistant_chat)
            db.session.commit()

            task_created = None
            if '```json' in assistant_content and '"automation"' in assistant_content:
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
                except (json.JSONDecodeError, ValueError):
                    pass

            return {
                "response": assistant_content,
                "task_created": task_created,
                "task_id": task_id
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
