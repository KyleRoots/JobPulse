"""
Audit — Audit note creation and change humanization.

Contains:
- _add_audit_notes: Create Bullhorn audit notes for executed changes
- _humanize_change_line: Convert raw change descriptions to human-readable format
- FIELD_LABELS: Human-readable field name mapping
- VALUE_LABELS: Human-readable value mapping for boolean/enum fields
"""

import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AuditMixin:
    """Audit note creation and change humanization."""

    FIELD_LABELS = {
        'ispublic': 'Published to Web',
        'isopen': 'Open Status',
        'isdeleted': 'Deleted',
        'status': 'Status',
        'employmenttype': 'Employment Type',
        'title': 'Title',
        'payrate': 'Pay Rate',
        'clientbillrate': 'Client Bill Rate',
        'salary': 'Salary',
        'salaryunit': 'Salary Unit',
        'startdate': 'Start Date',
        'dateclosed': 'Date Closed',
        'address': 'Location',
        'description': 'Description',
        'publicdescription': 'Public Description',
        'numOpenings': 'Number of Openings',
        'reasonclosed': 'Reason Closed',
        'customtext1': 'Custom Text 1',
        'customtext2': 'Custom Text 2',
        'customtext3': 'Custom Text 3',
    }

    VALUE_LABELS = {
        ('ispublic', '1'): 'Yes',
        ('ispublic', '0'): 'No',
        ('ispublic', 'True'): 'Yes',
        ('ispublic', 'False'): 'No',
        ('isopen', '1'): 'Yes',
        ('isopen', '0'): 'No',
        ('isdeleted', '1'): 'Yes',
        ('isdeleted', '0'): 'No',
    }

    def _add_audit_notes(self, ticket, proof_items: List[Dict], steps: list):
        from scout_support_service import CATEGORY_LABELS

        has_successful_changes = any(
            item.get('result', '').lower() == 'success'
            and not item.get('step', '').lower().startswith(('retrieved', 'searched', 'queried', 'get '))
            for item in proof_items
        )
        if not has_successful_changes:
            return

        noted_entities = set()
        note_entity_map = {
            'Candidate': 'candidates',
            'JobOrder': 'jobOrder',
            'ClientContact': 'clientContact',
            'Placement': 'placement',
        }

        for step in steps:
            entity_type = step.get('entity_type', '')
            entity_id = step.get('entity_id')
            action_type = step.get('action', '')

            if action_type in ('get_entity', 'search_entity', 'query_entity', 'get_associations', 'get_files'):
                continue
            if not entity_id or entity_type not in note_entity_map:
                continue

            key = (entity_type, str(entity_id))
            if key in noted_entities:
                continue
            noted_entities.add(key)

            try:
                change_lines = []
                for item in proof_items:
                    step_text = item.get('step', '')
                    if str(entity_id) in step_text and item.get('result', '').lower() == 'success':
                        old_val = item.get('old_value', '')
                        new_val = item.get('new_value', '')
                        if old_val and new_val:
                            change_lines.append(f"{step_text} (was: {old_val}, now: {new_val})")
                        else:
                            change_lines.append(step_text)

                if not change_lines:
                    continue

                friendly_lines = []
                for line in change_lines:
                    friendly = self._humanize_change_line(line, entity_type, entity_id)
                    if friendly:
                        friendly_lines.append(friendly)

                if not friendly_lines:
                    continue

                changes_summary = "\n".join(f"• {line}" for line in friendly_lines)
                category_display = CATEGORY_LABELS.get(ticket.category, ticket.category or 'General')
                note_text = (
                    f"━━━ Scout Support ━━━\n"
                    f"Ticket: {ticket.ticket_number}\n"
                    f"Request: {ticket.subject}\n"
                    f"Type: {category_display}\n"
                    f"\nWhat was done:\n{changes_summary}\n"
                    f"\nResolved by Scout Support AI."
                )

                assoc_field = note_entity_map[entity_type]
                api_user_id = self.bullhorn_service.user_id
                person_ref_id = self._resolve_person_reference(entity_type, int(entity_id), int(api_user_id) if api_user_id else 0)

                note_data = {
                    'action': self._get_bullhorn_note_action(entity_type),
                    'comments': note_text,
                    'isDeleted': False,
                    'personReference': {'id': int(person_ref_id)},
                }
                if api_user_id:
                    note_data['commentingPerson'] = {'id': int(api_user_id)}

                if entity_type == 'Candidate':
                    note_data['candidates'] = [{'id': int(entity_id)}]

                url = f"{self.bullhorn_service.base_url}entity/Note"
                params = {'BhRestToken': self.bullhorn_service.rest_token}
                response = self.bullhorn_service.session.put(url, params=params, json=note_data, timeout=60)

                if response.status_code in (200, 201):
                    data = response.json() if response.text else {}
                    note_id = data.get('changedEntityId', 'unknown')
                    logger.info(f"📝 Audit note #{note_id} created for {entity_type} #{entity_id} for ticket {ticket.ticket_number}")

                    if entity_type in ('JobOrder', 'Placement') and note_id and note_id != 'unknown':
                        self._link_note_via_note_entity(note_id, entity_type, entity_id, params)
                else:
                    logger.warning(f"⚠️ Audit note failed for {entity_type} #{entity_id}: HTTP {response.status_code} — {response.text[:200] if response.text else ''}")

            except Exception as e:
                logger.warning(f"⚠️ Audit note error for {entity_type} #{entity_id}: {e}")

    def _humanize_change_line(self, line: str, entity_type: str, entity_id) -> Optional[str]:
        lower = line.lower()
        if lower.startswith('retrieved') or lower.startswith('searched') or lower.startswith('queried'):
            return None
        if 'deferred' in lower:
            return None

        update_match = re.search(r'Updated \w+ #\d+:\s*(\w+)\s*\(was:\s*(.+?),\s*now:\s*(.+?)\)', line)
        if update_match:
            field_raw = update_match.group(1)
            old_val = update_match.group(2).strip()
            new_val = update_match.group(3).strip()

            field_label = self.FIELD_LABELS.get(field_raw.lower(), field_raw)
            old_display = self.VALUE_LABELS.get((field_raw.lower(), old_val), old_val)
            new_display = self.VALUE_LABELS.get((field_raw.lower(), new_val), new_val)

            return f"Changed {field_label} from {old_display} to {new_display}"

        create_match = re.search(r'Created (\w+) on', line)
        if create_match:
            created_type = create_match.group(1).lower()
            if created_type in ('note', 'notes'):
                return None
            return line

        line = re.sub(r'(?:JobOrder|Candidate|ClientContact|Placement) #\d+', '', line).strip()
        line = re.sub(r'\s+', ' ', line).strip(' :')
        return line if line else None
