"""
Execution — Bullhorn API action execution, entity CRUD, note creation.

Contains:
- _execute_solution: Top-level execution orchestrator
- _execute_bullhorn_actions: Iterates execution steps with runtime context
- _exec_update_entity_api: Raw Bullhorn entity update API call
- _exec_update_entity: Update with before/after capture and verification
- _exec_create_note: Note creation with personReference resolution
- _exec_create_submission: JobSubmission creation
- _exec_search_entity: Entity search
- _exec_get_entity: Entity read
- _exec_remove_from_tearsheet: Tearsheet removal
- _exec_delete_entity: Soft/hard delete
- _exec_bulk_update: Bulk entity update
- _exec_bulk_delete: Bulk entity delete
- _exec_create_entity: Generic entity creation (with Note special handling)
- _exec_add_association: To-many association add
- _exec_remove_association: To-many association remove
- _exec_add_to_tearsheet: Tearsheet add (job or candidate)
- _exec_query_entity: BQL query
- _exec_get_associations: To-many field read
- _exec_get_files: File listing
- _exec_delete_file: File deletion
- _coerce_bullhorn_value: Type coercion for Bullhorn fields
- _resolve_person_reference: Resolve personReference for Note creation
- _get_bullhorn_note_action: Note action type lookup
- _link_note_via_note_entity: NoteEntity linking for JobOrder/Placement notes
"""

import json
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


class ExecutionMixin:
    """Bullhorn API action execution, entity CRUD, and note creation."""

    BULLHORN_NOTE_ACTIONS = {
        'JobOrder': 'Job Update',
        'Candidate': 'General Notes',
        'ClientContact': 'General Notes',
        'Placement': 'General Notes',
    }

    def _execute_solution(self, ticket) -> bool:
        from extensions import db
        from models import SupportAction

        logger.info(f"🔧 Executing solution for ticket {ticket.ticket_number}")

        try:
            understanding = json.loads(ticket.ai_understanding) if ticket.ai_understanding else {}
        except (json.JSONDecodeError, TypeError):
            understanding = {}

        try:
            solution_data = json.loads(ticket.proposed_solution) if ticket.proposed_solution else {}
        except (json.JSONDecodeError, TypeError):
            solution_data = {'description': ticket.proposed_solution}

        execution_steps = solution_data.get('execution_steps', [])
        requires_bullhorn = (
            solution_data.get('requires_bullhorn', False)
            or understanding.get('requires_bullhorn_api', False)
        )

        if requires_bullhorn and self.bullhorn_service and execution_steps:
            proof_items = self._execute_bullhorn_actions(ticket, solution_data)
        elif requires_bullhorn and not self.bullhorn_service:
            logger.warning(f"Ticket {ticket.ticket_number} requires Bullhorn but service unavailable — re-initializing")
            self.bullhorn_service = self._init_bullhorn()
            if self.bullhorn_service and execution_steps:
                proof_items = self._execute_bullhorn_actions(ticket, solution_data)
            else:
                proof_items = [{'step': 'Bullhorn connection failed', 'result': 'Failed: Could not connect to Bullhorn — manual resolution required'}]
        elif requires_bullhorn and not execution_steps:
            logger.warning(f"Ticket {ticket.ticket_number} requires Bullhorn but no execution steps defined")
            proof_items = [{'step': 'No execution steps defined', 'result': 'Failed: AI did not generate actionable steps — manual resolution required'}]
        else:
            proof_items = [{'step': 'Manual guidance provided', 'result': 'User-side resolution'}]

        proof_summary = json.dumps(proof_items, indent=2)
        ticket.execution_proof = proof_summary

        has_failures = any(
            'fail' in item.get('result', '').lower()
            for item in proof_items
        )

        diagnostic_only = all(
            step.get('action') in ('get_entity', 'search_entity', 'query_entity', 'get_associations', 'get_files')
            for step in execution_steps
        ) if execution_steps else False

        if has_failures:
            ticket.status = 'execution_failed'
            db.session.commit()
            logger.warning(f"⚠️ Ticket {ticket.ticket_number} execution had failures")
            return False
        elif diagnostic_only and execution_steps:
            ticket.status = 'completed'
            ticket.resolved_at = datetime.utcnow()
            resolution_type = solution_data.get('resolution_type', 'full')
            if resolution_type == 'partial':
                ticket.status = 'completed'
            db.session.commit()
            self._send_completion_email(ticket, proof_items)
            logger.info(f"✅ Ticket {ticket.ticket_number} completed (diagnostic steps executed, resolution_type={resolution_type})")
            return True
        else:
            ticket.status = 'completed'
            ticket.resolved_at = datetime.utcnow()
            db.session.commit()
            self._send_completion_email(ticket, proof_items)
            logger.info(f"✅ Ticket {ticket.ticket_number} completed successfully")
            return True

    def _execute_bullhorn_actions(self, ticket, solution_data: dict) -> List[Dict]:
        from extensions import db
        from models import SupportAction

        proof_items = []
        steps = solution_data.get('execution_steps', [])
        runtime_context = {}

        for step in steps:
            action_type = step.get('action', 'unknown')
            entity_type = step.get('entity_type', 'Candidate')
            entity_id = step.get('entity_id')
            field = step.get('field')
            new_value = step.get('new_value')
            desc = step.get('description', 'Unknown step')

            if isinstance(new_value, str) and new_value.startswith('{{') and new_value.endswith('}}'):
                ref_key = new_value[2:-2].strip()
                resolved = runtime_context.get(ref_key)
                if resolved is not None:
                    new_value = resolved
                    logger.info(f"🔄 Resolved runtime reference {ref_key} → {str(new_value)[:100]}")

            fallback_field = step.get('fallback_field')
            if fallback_field and entity_id and (not new_value or new_value == ''):
                ctx_key = f"{entity_type}_{entity_id}"
                entity_data = runtime_context.get(ctx_key, {})
                if entity_data:
                    fallback_val = entity_data.get(fallback_field)
                    if fallback_val:
                        new_value = fallback_val
                        logger.info(f"🔄 Used fallback field {fallback_field} for {entity_type} #{entity_id}")

            action = SupportAction(
                ticket_id=ticket.id,
                action_type=action_type,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id else None,
                field_name=field,
                old_value=step.get('old_value'),
                new_value=str(new_value) if new_value is not None else None,
                summary=desc,
            )

            try:
                if action_type == 'update_entity' and entity_id and field:
                    result = self._exec_update_entity(action, entity_type, int(entity_id), field, new_value)
                    proof_items.append(result)

                elif action_type == 'create_note' and entity_id:
                    logger.info(f"📝 Skipping AI-generated create_note step — audit note will be created automatically")
                    action.success = True
                    action.new_value = 'Deferred to audit note'
                    proof_items.append({'step': step.get('description', 'Create note'), 'result': 'Deferred — audit note handles this'})
                    continue

                elif action_type == 'create_submission':
                    result = self._exec_create_submission(action, step)
                    proof_items.append(result)

                elif action_type == 'search_entity':
                    result = self._exec_search_entity(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'get_entity' and entity_id:
                    result = self._exec_get_entity(action, entity_type, int(entity_id), step)
                    proof_items.append(result)
                    if result.get('data'):
                        ctx_key = f"{entity_type}_{entity_id}"
                        runtime_context[ctx_key] = result['data']
                        for k, v in result['data'].items():
                            runtime_context[f"{entity_type}_{entity_id}_{k}"] = v

                elif action_type == 'remove_from_tearsheet':
                    result = self._exec_remove_from_tearsheet(action, step)
                    proof_items.append(result)

                elif action_type == 'delete_entity' and entity_id:
                    result = self._exec_delete_entity(action, entity_type, int(entity_id), step)
                    proof_items.append(result)

                elif action_type == 'bulk_update':
                    result = self._exec_bulk_update(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'bulk_delete':
                    result = self._exec_bulk_delete(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'create_entity':
                    if entity_type == 'Note':
                        logger.info(f"📝 Skipping AI-generated note step — audit note will be created automatically")
                        action.success = True
                        action.new_value = 'Deferred to audit note'
                        proof_items.append({'step': step.get('description', 'Create note'), 'result': 'Deferred — audit note handles this'})
                        continue
                    result = self._exec_create_entity(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'add_association':
                    result = self._exec_add_association(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'remove_association':
                    result = self._exec_remove_association(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'add_to_tearsheet':
                    result = self._exec_add_to_tearsheet(action, step)
                    proof_items.append(result)

                elif action_type == 'query_entity':
                    result = self._exec_query_entity(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'get_associations':
                    result = self._exec_get_associations(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'get_files':
                    result = self._exec_get_files(action, entity_type, step)
                    proof_items.append(result)

                elif action_type == 'delete_file':
                    result = self._exec_delete_file(action, entity_type, step)
                    proof_items.append(result)

                else:
                    action.success = True
                    action.summary = desc
                    proof_items.append({'step': desc, 'result': 'Guidance provided'})

            except Exception as e:
                action.success = False
                action.error_message = str(e)
                proof_items.append({'step': desc, 'result': f'Failed: {str(e)}'})
                logger.error(f"Bullhorn action failed for ticket {ticket.ticket_number}: {e}")

            db.session.add(action)

        db.session.commit()

        self._add_audit_notes(ticket, proof_items, steps)

        return proof_items

    def _coerce_bullhorn_value(self, field: str, value):
        bool_int_fields = {
            'ispublic', 'isopen', 'isdeleted', 'isprivate', 'islockedout',
            'isbillablechargecardentry', 'isinterviewrequired', 'isclientcontact',
        }
        if field.lower() in bool_int_fields:
            if isinstance(value, bool):
                return 1 if value else 0
            if isinstance(value, str) and value.lower() in ('true', 'false'):
                return 1 if value.lower() == 'true' else 0
        if isinstance(value, str):
            try:
                if '.' in value:
                    return float(value)
                return int(value)
            except (ValueError, TypeError):
                pass
        return value

    def _exec_update_entity_api(self, entity_type: str, entity_id: int, field: str, new_value) -> Dict:
        try:
            url = f"{self.bullhorn_service.base_url}entity/{entity_type}/{entity_id}"
            params = {'BhRestToken': self.bullhorn_service.rest_token}
            response = self.bullhorn_service.session.post(url, params=params, json={field: new_value}, timeout=30)

            if response.status_code == 401:
                if self.bullhorn_service.authenticate():
                    params['BhRestToken'] = self.bullhorn_service.rest_token
                    response = self.bullhorn_service.session.post(url, params=params, json={field: new_value}, timeout=30)
                else:
                    return {'success': False, 'error': 'Authentication failed'}

            if response.status_code == 200:
                return {'success': True}
            else:
                error_text = ''
                try:
                    resp_data = response.json()
                    error_text = resp_data.get('errorMessage', resp_data.get('message', ''))
                    if not error_text and 'errors' in resp_data:
                        error_text = '; '.join(str(e) for e in resp_data['errors'][:3])
                except Exception:
                    error_text = response.text[:200] if response.text else ''
                return {'success': False, 'error': f'HTTP {response.status_code}: {error_text}' if error_text else f'HTTP {response.status_code}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _exec_update_entity(self, action, entity_type: str, entity_id: int, field: str, new_value) -> Dict:
        new_value = self._coerce_bullhorn_value(field, new_value)

        current = self.bullhorn_service.get_entity(entity_type, entity_id)
        if current:
            action.old_value = str(current.get(field, ''))

        update_result = self._exec_update_entity_api(entity_type, entity_id, field, new_value)
        if update_result['success']:
            verified = self.bullhorn_service.get_entity(entity_type, entity_id, fields=f'id,{field}')
            verified_value = verified.get(field, 'unknown') if verified else 'unknown'
            action.success = True
            logger.info(f"✅ Bullhorn update: {entity_type} #{entity_id} {field}: {action.old_value} → {new_value} (verified: {verified_value})")
            return {
                'step': f"Updated {entity_type} #{entity_id}: {field}",
                'old_value': action.old_value,
                'new_value': str(new_value),
                'verified_value': str(verified_value),
                'result': 'Success',
            }
        else:
            error_detail = update_result.get('error', 'Bullhorn API returned failure')
            action.success = False
            action.error_message = error_detail
            logger.error(f"❌ Bullhorn update failed: {entity_type} #{entity_id} {field} — {error_detail}")
            return {'step': f"Update {entity_type} #{entity_id}: {field}", 'result': f'Failed — {error_detail}'}

    def _exec_create_note(self, action, entity_type: str, entity_id: int, step: dict) -> Dict:
        note_text = step.get('note_text', step.get('new_value', ''))
        note_action = step.get('note_action', 'Scout Support')
        if not note_text:
            action.success = False
            action.error_message = 'Missing note_text'
            return {'step': step.get('description', 'Create note'), 'result': 'Failed — no note text provided'}

        api_user_id = self.bullhorn_service.user_id
        if not api_user_id:
            action.success = False
            action.error_message = 'No Bullhorn API user ID available for note creation'
            return {'step': step.get('description', 'Create note'), 'result': 'Failed — no API user ID'}

        person_ref_id = self._resolve_person_reference(entity_type, entity_id, api_user_id)

        note_data = {
            'action': self._get_bullhorn_note_action(entity_type),
            'comments': note_text,
            'isDeleted': False,
            'personReference': {'id': int(person_ref_id)},
            'commentingPerson': {'id': int(api_user_id)},
        }

        if entity_type == 'Candidate':
            note_data['candidates'] = [{'id': int(entity_id)}]

        url = f"{self.bullhorn_service.base_url}entity/Note"
        params = {'BhRestToken': self.bullhorn_service.rest_token}
        response = self.bullhorn_service.session.put(url, params=params, json=note_data, timeout=60)

        if response.status_code == 401:
            if self.bullhorn_service.authenticate():
                params['BhRestToken'] = self.bullhorn_service.rest_token
                url = f"{self.bullhorn_service.base_url}entity/Note"
                response = self.bullhorn_service.session.put(url, params=params, json=note_data, timeout=60)
            else:
                action.success = False
                action.error_message = 'Bullhorn re-authentication failed'
                return {'step': step.get('description', 'Create note'), 'result': 'Failed — authentication error'}

        if response.status_code in (200, 201):
            data = response.json() if response.text else {}
            note_id = data.get('changedEntityId', 'unknown')
            logger.info(f"📝 Note #{note_id} created for {entity_type} #{entity_id}")

            if entity_type in ('JobOrder', 'Placement') and note_id and note_id != 'unknown':
                self._link_note_via_note_entity(note_id, entity_type, entity_id, params)

            action.success = True
            action.new_value = f"Note #{note_id}"
            return {'step': f"Created note on {entity_type} #{entity_id}", 'note_id': note_id, 'result': 'Success'}
        else:
            error_detail = response.text[:200] if response.text else 'No response body'
            action.success = False
            action.error_message = f'Note creation failed: HTTP {response.status_code} — {error_detail}'
            logger.error(f"❌ Note creation failed for {entity_type} #{entity_id}: HTTP {response.status_code} — {error_detail}")
            return {'step': f"Create note on {entity_type} #{entity_id}", 'result': f'Failed — HTTP {response.status_code}'}

    def _resolve_person_reference(self, entity_type: str, entity_id: int, fallback_user_id: int) -> int:
        if entity_type == 'Candidate':
            return entity_id
        if entity_type == 'ClientContact':
            return entity_id
        if entity_type == 'JobOrder':
            try:
                params = {'BhRestToken': self.bullhorn_service.rest_token}
                url = f"{self.bullhorn_service.base_url}entity/JobOrder/{entity_id}?fields=clientContact"
                resp = self.bullhorn_service.session.get(url, params=params, timeout=15)
                if resp.status_code == 200:
                    data = resp.json().get('data', {})
                    contact = data.get('clientContact', {})
                    if contact and contact.get('id'):
                        logger.info(f"📝 Resolved personReference for JobOrder #{entity_id}: ClientContact #{contact['id']}")
                        return contact['id']
            except Exception as e:
                logger.warning(f"⚠️ Failed to resolve ClientContact for JobOrder #{entity_id}: {e}")
        if entity_type == 'Placement':
            try:
                params = {'BhRestToken': self.bullhorn_service.rest_token}
                url = f"{self.bullhorn_service.base_url}entity/Placement/{entity_id}?fields=candidate"
                resp = self.bullhorn_service.session.get(url, params=params, timeout=15)
                if resp.status_code == 200:
                    data = resp.json().get('data', {})
                    candidate = data.get('candidate', {})
                    if candidate and candidate.get('id'):
                        logger.info(f"📝 Resolved personReference for Placement #{entity_id}: Candidate #{candidate['id']}")
                        return candidate['id']
            except Exception as e:
                logger.warning(f"⚠️ Failed to resolve Candidate for Placement #{entity_id}: {e}")
        return fallback_user_id

    def _get_bullhorn_note_action(self, entity_type: str) -> str:
        return self.BULLHORN_NOTE_ACTIONS.get(entity_type, 'General Notes')

    def _link_note_via_note_entity(self, note_id, entity_type: str, entity_id: int, params: dict):
        logger.info(f"📝 Attempting to link Note #{note_id} → {entity_type} #{entity_id}")

        note_entity_data = {
            'note': {'id': int(note_id)},
            'targetEntityID': int(entity_id),
            'targetEntityName': entity_type,
        }
        ne_url = f"{self.bullhorn_service.base_url}entity/NoteEntity"
        try:
            ne_resp = self.bullhorn_service.session.put(ne_url, params=params, json=note_entity_data, timeout=30)
            if ne_resp.status_code == 200:
                logger.info(f"📝 NoteEntity link OK: Note #{note_id} → {entity_type} #{entity_id}")
            else:
                ne_body = ne_resp.text[:300] if ne_resp.text else 'empty'
                logger.warning(f"⚠️ NoteEntity link HTTP {ne_resp.status_code}: {ne_body}")
        except Exception as e:
            logger.warning(f"⚠️ NoteEntity link error: {e}")

    def _exec_create_submission(self, action, step: dict) -> Dict:
        candidate_id = step.get('candidate_id')
        job_id = step.get('job_id')
        source = step.get('source', 'Scout Support')
        if not candidate_id or not job_id:
            action.success = False
            action.error_message = 'Missing candidate_id or job_id'
            return {'step': step.get('description', 'Create submission'), 'result': 'Failed — missing candidate or job ID'}

        submission_id = self.bullhorn_service.create_job_submission(int(candidate_id), int(job_id), source=source)
        if submission_id:
            action.success = True
            action.new_value = f"Submission #{submission_id}"
            logger.info(f"✅ Created submission #{submission_id}: Candidate #{candidate_id} → Job #{job_id}")
            return {'step': f"Submitted Candidate #{candidate_id} to Job #{job_id}", 'submission_id': submission_id, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Submission creation failed'
            return {'step': step.get('description', 'Create submission'), 'result': 'Failed — API error'}

    def _exec_search_entity(self, action, entity_type: str, step: dict) -> Dict:
        query = step.get('query', '')
        if not query:
            action.success = False
            action.error_message = 'Missing search query'
            return {'step': step.get('description', 'Search'), 'result': 'Failed — no query provided'}

        results = self.bullhorn_service.search_entity(entity_type, query, count=step.get('count', 10))
        action.success = True
        action.new_value = f"{len(results)} results"
        return {'step': f"Searched {entity_type}: {query}", 'result_count': len(results), 'results': results[:5], 'result': 'Success'}

    def _exec_get_entity(self, action, entity_type: str, entity_id: int, step: dict) -> Dict:
        fields = step.get('fields')
        data = self.bullhorn_service.get_entity(entity_type, entity_id, fields=fields)
        if not data and fields:
            logger.warning(f"⚠️ GET {entity_type} #{entity_id} failed with custom fields, retrying with defaults")
            data = self.bullhorn_service.get_entity(entity_type, entity_id)
        if data:
            action.success = True
            action.new_value = json.dumps(data)[:500]
            return {'step': f"Retrieved {entity_type} #{entity_id}", 'data': data, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = f'{entity_type} #{entity_id} not found'
            return {'step': f"Get {entity_type} #{entity_id}", 'result': 'Failed — entity not found'}

    def _exec_remove_from_tearsheet(self, action, step: dict) -> Dict:
        tearsheet_id = step.get('tearsheet_id')
        job_id = step.get('job_id')
        if not tearsheet_id or not job_id:
            action.success = False
            action.error_message = 'Missing tearsheet_id or job_id'
            return {'step': step.get('description', 'Remove from tearsheet'), 'result': 'Failed — missing IDs'}

        success = self.bullhorn_service.remove_job_from_tearsheet(int(tearsheet_id), int(job_id))
        if success:
            action.success = True
            logger.info(f"✅ Removed Job #{job_id} from Tearsheet #{tearsheet_id}")
            return {'step': f"Removed Job #{job_id} from Tearsheet #{tearsheet_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Remove from tearsheet failed'
            return {'step': step.get('description', 'Remove from tearsheet'), 'result': 'Failed — API error'}

    def _exec_delete_entity(self, action, entity_type: str, entity_id: int, step: dict) -> Dict:
        soft = step.get('soft_delete', True)
        current = self.bullhorn_service.get_entity(entity_type, entity_id)
        if current:
            action.old_value = json.dumps({k: v for k, v in current.items() if k in ('id', 'status', 'isDeleted', 'firstName', 'lastName', 'title', 'name')})[:500]

        success = self.bullhorn_service.delete_entity(entity_type, entity_id, soft_delete=soft)
        mode = 'soft-deleted' if soft else 'hard-deleted'
        if success:
            action.success = True
            logger.info(f"✅ {mode.title()} {entity_type} #{entity_id}")
            return {'step': f"{mode.title()} {entity_type} #{entity_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = f'{mode.title()} failed'
            return {'step': f"Delete {entity_type} #{entity_id}", 'result': f'Failed — {mode} error'}

    def _exec_bulk_update(self, action, entity_type: str, step: dict) -> Dict:
        entity_ids = step.get('entity_ids', [])
        update_data = step.get('update_data', {})
        if not entity_ids or not update_data:
            action.success = False
            action.error_message = 'Missing entity_ids or update_data'
            return {'step': step.get('description', 'Bulk update'), 'result': 'Failed — missing IDs or data'}

        int_ids = [int(eid) for eid in entity_ids]
        results = self.bullhorn_service.bulk_update_entities(entity_type, int_ids, update_data)
        succeeded = sum(1 for v in results.values() if v)
        failed = len(int_ids) - succeeded
        action.success = failed == 0
        action.new_value = f"{succeeded}/{len(int_ids)} succeeded"
        if failed > 0:
            action.error_message = f"{failed} updates failed"
        logger.info(f"{'✅' if failed == 0 else '⚠️'} Bulk update {entity_type}: {succeeded}/{len(int_ids)} succeeded")
        return {
            'step': f"Bulk updated {entity_type}: {list(update_data.keys())}",
            'total': len(int_ids), 'succeeded': succeeded, 'failed': failed,
            'result': 'Success' if failed == 0 else f'Partial — {failed} failed',
        }

    def _exec_bulk_delete(self, action, entity_type: str, step: dict) -> Dict:
        entity_ids = step.get('entity_ids', [])
        soft = step.get('soft_delete', True)
        if not entity_ids:
            action.success = False
            action.error_message = 'Missing entity_ids'
            return {'step': step.get('description', 'Bulk delete'), 'result': 'Failed — no IDs provided'}

        int_ids = [int(eid) for eid in entity_ids]
        results = self.bullhorn_service.bulk_delete_entities(entity_type, int_ids, soft_delete=soft)
        succeeded = sum(1 for v in results.values() if v)
        failed = len(int_ids) - succeeded
        mode = 'soft-deleted' if soft else 'hard-deleted'
        action.success = failed == 0
        action.new_value = f"{succeeded}/{len(int_ids)} {mode}"
        if failed > 0:
            action.error_message = f"{failed} deletes failed"
        logger.info(f"{'✅' if failed == 0 else '⚠️'} Bulk {mode} {entity_type}: {succeeded}/{len(int_ids)}")
        return {
            'step': f"Bulk {mode} {entity_type}",
            'total': len(int_ids), 'succeeded': succeeded, 'failed': failed,
            'result': 'Success' if failed == 0 else f'Partial — {failed} failed',
        }

    def _exec_create_entity(self, action, entity_type: str, step: dict) -> Dict:
        entity_data = step.get('entity_data', {})

        if entity_type == 'Note':
            target_entity_id = step.get('target_entity_id') or step.get('entity_id')
            target_entity_type = step.get('target_entity_type', 'JobOrder')
            note_text = entity_data.get('comments', entity_data.get('note_text', step.get('note_text', '')))
            note_action = entity_data.get('action', 'Scout Support')

            if not target_entity_id:
                for assoc_field in ('jobOrders', 'candidates', 'clientContacts', 'placements'):
                    assoc_val = entity_data.get(assoc_field)
                    if assoc_val:
                        if isinstance(assoc_val, list) and len(assoc_val) > 0:
                            target_entity_id = assoc_val[0].get('id') if isinstance(assoc_val[0], dict) else assoc_val[0]
                        elif isinstance(assoc_val, dict):
                            target_entity_id = assoc_val.get('id')
                        type_map = {'jobOrders': 'JobOrder', 'candidates': 'Candidate', 'clientContacts': 'ClientContact', 'placements': 'Placement'}
                        target_entity_type = type_map.get(assoc_field, target_entity_type)
                        break

            note_step = {
                'note_text': note_text,
                'note_action': note_action,
                'description': step.get('description', 'Create note'),
            }
            if target_entity_id:
                return self._exec_create_note(action, target_entity_type, int(target_entity_id), note_step)

            api_user_id = self.bullhorn_service.user_id
            if api_user_id and 'personReference' not in entity_data:
                entity_data['personReference'] = {'id': int(api_user_id)}
                entity_data['commentingPerson'] = {'id': int(api_user_id)}

        if not entity_data:
            action.success = False
            action.error_message = 'Missing entity_data'
            return {'step': step.get('description', 'Create entity'), 'result': 'Failed — no data provided'}

        new_id = self.bullhorn_service.create_entity(entity_type, entity_data)
        if new_id:
            action.success = True
            action.new_value = f"{entity_type} #{new_id}"
            logger.info(f"✅ Created {entity_type} #{new_id}")
            return {'step': f"Created {entity_type} #{new_id}", 'entity_id': new_id, 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Entity creation failed'
            return {'step': step.get('description', 'Create entity'), 'result': 'Failed — API error'}

    def _exec_add_association(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        association_field = step.get('association_field')
        associated_ids = step.get('associated_ids', [])
        if not entity_id or not association_field or not associated_ids:
            action.success = False
            action.error_message = 'Missing entity_id, association_field, or associated_ids'
            return {'step': step.get('description', 'Add association'), 'result': 'Failed — missing parameters'}

        success = self.bullhorn_service.add_entity_to_association(entity_type, int(entity_id), association_field, [int(i) for i in associated_ids])
        if success:
            action.success = True
            logger.info(f"✅ Added {association_field} association on {entity_type} #{entity_id}")
            return {'step': f"Added {association_field} on {entity_type} #{entity_id}: {associated_ids}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Association add failed'
            return {'step': step.get('description', 'Add association'), 'result': 'Failed — API error'}

    def _exec_remove_association(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        association_field = step.get('association_field')
        associated_ids = step.get('associated_ids', [])
        if not entity_id or not association_field or not associated_ids:
            action.success = False
            action.error_message = 'Missing entity_id, association_field, or associated_ids'
            return {'step': step.get('description', 'Remove association'), 'result': 'Failed — missing parameters'}

        success = self.bullhorn_service.remove_entity_from_association(entity_type, int(entity_id), association_field, [int(i) for i in associated_ids])
        if success:
            action.success = True
            logger.info(f"✅ Removed {association_field} association on {entity_type} #{entity_id}")
            return {'step': f"Removed {association_field} on {entity_type} #{entity_id}: {associated_ids}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Association remove failed'
            return {'step': step.get('description', 'Remove association'), 'result': 'Failed — API error'}

    def _exec_add_to_tearsheet(self, action, step: dict) -> Dict:
        tearsheet_id = step.get('tearsheet_id')
        job_id = step.get('job_id')
        candidate_id = step.get('candidate_id')
        if not tearsheet_id:
            action.success = False
            action.error_message = 'Missing tearsheet_id'
            return {'step': step.get('description', 'Add to tearsheet'), 'result': 'Failed — missing tearsheet ID'}

        if job_id:
            success = self.bullhorn_service.add_job_to_tearsheet(int(tearsheet_id), int(job_id))
            label = f"Job #{job_id}"
        elif candidate_id:
            success = self.bullhorn_service.add_candidate_to_tearsheet(int(tearsheet_id), int(candidate_id))
            label = f"Candidate #{candidate_id}"
        else:
            action.success = False
            action.error_message = 'Missing job_id or candidate_id'
            return {'step': step.get('description', 'Add to tearsheet'), 'result': 'Failed — missing job or candidate ID'}

        if success:
            action.success = True
            logger.info(f"✅ Added {label} to Tearsheet #{tearsheet_id}")
            return {'step': f"Added {label} to Tearsheet #{tearsheet_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'Add to tearsheet failed'
            return {'step': step.get('description', 'Add to tearsheet'), 'result': 'Failed — API error'}

    def _exec_query_entity(self, action, entity_type: str, step: dict) -> Dict:
        where = step.get('where', '')
        fields = step.get('fields', 'id')
        count = step.get('count', 50)
        if not where:
            action.success = False
            action.error_message = 'Missing WHERE clause'
            return {'step': step.get('description', 'Query'), 'result': 'Failed — no WHERE clause'}

        results = self.bullhorn_service.query_entity(entity_type, where, fields=fields, count=count)
        action.success = True
        action.new_value = f"{len(results)} results"
        return {'step': f"Queried {entity_type}: {where[:100]}", 'result_count': len(results), 'results': results[:10], 'result': 'Success'}

    def _exec_get_associations(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        association_field = step.get('association_field')
        fields = step.get('fields', 'id')
        if not entity_id or not association_field:
            action.success = False
            action.error_message = 'Missing entity_id or association_field'
            return {'step': step.get('description', 'Get associations'), 'result': 'Failed — missing parameters'}

        results = self.bullhorn_service.get_entity_associations(entity_type, int(entity_id), association_field, fields=fields)
        action.success = True
        action.new_value = f"{len(results)} associations"
        return {'step': f"Got {association_field} for {entity_type} #{entity_id}", 'result_count': len(results), 'results': results[:10], 'result': 'Success'}

    def _exec_get_files(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        if not entity_id:
            action.success = False
            action.error_message = 'Missing entity_id'
            return {'step': step.get('description', 'Get files'), 'result': 'Failed — missing entity ID'}

        files = self.bullhorn_service.get_entity_files(entity_type, int(entity_id))
        action.success = True
        action.new_value = f"{len(files)} files"
        return {'step': f"Listed files for {entity_type} #{entity_id}", 'result_count': len(files), 'files': files[:20], 'result': 'Success'}

    def _exec_delete_file(self, action, entity_type: str, step: dict) -> Dict:
        entity_id = step.get('entity_id')
        file_id = step.get('file_id')
        if not entity_id or not file_id:
            action.success = False
            action.error_message = 'Missing entity_id or file_id'
            return {'step': step.get('description', 'Delete file'), 'result': 'Failed — missing IDs'}

        success = self.bullhorn_service.delete_entity_file(entity_type, int(entity_id), int(file_id))
        if success:
            action.success = True
            logger.info(f"✅ Deleted file #{file_id} from {entity_type} #{entity_id}")
            return {'step': f"Deleted file #{file_id} from {entity_type} #{entity_id}", 'result': 'Success'}
        else:
            action.success = False
            action.error_message = 'File deletion failed'
            return {'step': step.get('description', 'Delete file'), 'result': 'Failed — API error'}
