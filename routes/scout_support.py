import json
import logging
from datetime import datetime

from flask import Blueprint, render_template, jsonify, request, redirect, url_for
from flask_login import login_required, current_user
from extensions import db
from routes import register_module_guard

scout_support_bp = Blueprint('scout_support', __name__)
register_module_guard(scout_support_bp, 'scout_support')
logger = logging.getLogger(__name__)


@scout_support_bp.route('/scout-support')
@login_required
def scout_support_dashboard():
    from models import SupportTicket

    if current_user.is_admin:
        tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    else:
        tickets = SupportTicket.query.filter_by(
            submitter_email=current_user.email
        ).order_by(SupportTicket.created_at.desc()).all()

    pending_approval = SupportTicket.query.filter(
        SupportTicket.status.in_(['awaiting_admin_approval'])
    ).count() if current_user.is_admin else 0

    stats = {
        'total': len(tickets),
        'open': sum(1 for t in tickets if t.status not in ('completed', 'closed')),
        'completed': sum(1 for t in tickets if t.status == 'completed'),
        'pending_approval': pending_approval,
    }

    return render_template('scout_support_dashboard.html',
                           tickets=tickets,
                           stats=stats,
                           active_page='scout_support')


@scout_support_bp.route('/scout-support/ticket/<ticket_number>')
@login_required
def ticket_detail(ticket_number):
    from models import SupportTicket

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()

    if not current_user.is_admin and ticket.submitter_email != current_user.email:
        return redirect(url_for('scout_support.scout_support_dashboard'))

    conversations = ticket.conversations.order_by(db.text('created_at ASC')).all()
    actions = ticket.actions.order_by(db.text('executed_at ASC')).all()

    ai_understanding = None
    if ticket.ai_understanding:
        try:
            ai_understanding = json.loads(ticket.ai_understanding)
        except (json.JSONDecodeError, TypeError):
            ai_understanding = {'understanding': ticket.ai_understanding}

    return render_template('scout_support_ticket.html',
                           ticket=ticket,
                           conversations=conversations,
                           actions=actions,
                           ai_understanding=ai_understanding,
                           active_page='scout_support')


@scout_support_bp.route('/api/scout-support/tickets')
@login_required
def api_tickets():
    from models import SupportTicket

    if current_user.is_admin:
        tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).limit(100).all()
    else:
        tickets = SupportTicket.query.filter_by(
            submitter_email=current_user.email
        ).order_by(SupportTicket.created_at.desc()).limit(100).all()

    return jsonify([{
        'ticket_number': t.ticket_number,
        'subject': t.subject,
        'category': t.category,
        'status': t.status,
        'priority': t.priority,
        'submitter_name': t.submitter_name,
        'created_at': t.created_at.isoformat() if t.created_at else None,
        'updated_at': t.updated_at.isoformat() if t.updated_at else None,
    } for t in tickets])


@scout_support_bp.route('/api/scout-support/escalate/<ticket_number>', methods=['POST'])
@login_required
def api_escalate_ticket(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    reason = request.json.get('reason', 'Manual escalation by admin') if request.is_json else 'Manual escalation by admin'
    svc = ScoutSupportService()
    success = svc.escalate_ticket(ticket.id, reason)

    return jsonify({'success': success})


@scout_support_bp.route('/api/scout-support/close/<ticket_number>', methods=['POST'])
@login_required
def api_close_ticket(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    data = request.json or {}
    resolution_note = data.get('resolution_note', '').strip()
    action = data.get('action', 'close')

    if not resolution_note:
        return jsonify({'error': 'A resolution note is required'}), 400

    new_status = 'completed' if action == 'resolve' else 'closed'

    svc = ScoutSupportService()
    success = svc.close_ticket(
        ticket_id=ticket.id,
        resolution_note=resolution_note,
        closed_by=current_user.email,
        new_status=new_status,
    )

    return jsonify({'success': success})


@scout_support_bp.route('/api/scout-support/retry/<ticket_number>', methods=['POST'])
@login_required
def api_retry_execution(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    svc = ScoutSupportService()
    result = svc.retry_execution(ticket.id)

    return jsonify(result)


@scout_support_bp.route('/scout-support/ticket/<ticket_number>/delete', methods=['POST'])
@login_required
def delete_ticket(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket, SupportConversation, SupportAction

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    SupportAction.query.filter_by(ticket_id=ticket.id).delete()
    SupportConversation.query.filter_by(ticket_id=ticket.id).delete()
    db.session.delete(ticket)
    db.session.commit()

    logger.info(f"🗑️ Admin deleted ticket {ticket_number}")
    return jsonify({'success': True, 'message': f'Ticket {ticket_number} deleted'})


@scout_support_bp.route('/scout-support/diag/note/<int:note_id>')
@login_required
def diag_note(note_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin only'}), 403
    from bullhorn_service import BullhornService
    bh = BullhornService()
    if not bh.authenticate():
        return jsonify({'error': 'Bullhorn auth failed'}), 500
    params = {'BhRestToken': bh.rest_token}
    all_fields = 'id,action,comments,personReference,commentingPerson,jobOrders,candidates,clientContacts,isDeleted,dateAdded,dateLastModified'
    url = f"{bh.base_url}entity/Note/{note_id}?fields={all_fields}"
    resp = bh.session.get(url, params=params, timeout=30)
    return jsonify({'status': resp.status_code, 'note': resp.json() if resp.text else {}})


@scout_support_bp.route('/scout-support/diag/jo-notes/<int:job_id>')
@login_required
def diag_jo_notes(job_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin only'}), 403
    from bullhorn_service import BullhornService
    bh = BullhornService()
    if not bh.authenticate():
        return jsonify({'error': 'Bullhorn auth failed'}), 500
    params = {'BhRestToken': bh.rest_token, 'fields': 'id,action,comments,personReference,commentingPerson,isDeleted,dateAdded', 'count': '20'}
    url = f"{bh.base_url}entity/JobOrder/{job_id}/notes"
    resp = bh.session.get(url, params=params, timeout=30)
    return jsonify({'status': resp.status_code, 'notes': resp.json() if resp.text else {}})


@scout_support_bp.route('/scout-support/diag/create-test-note/<int:job_id>')
@login_required
def diag_create_test_note(job_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin only'}), 403
    from bullhorn_service import BullhornService
    bh = BullhornService()
    if not bh.authenticate():
        return jsonify({'error': 'Bullhorn auth failed'}), 500
    params = {'BhRestToken': bh.rest_token}
    results = {}

    jo_url = f"{bh.base_url}entity/JobOrder/{job_id}?fields=id,clientContact"
    jo_resp = bh.session.get(jo_url, params=params, timeout=30)
    jo_data = jo_resp.json().get('data', {}) if jo_resp.text else {}
    contact_id = jo_data.get('clientContact', {}).get('id') if jo_data.get('clientContact') else None
    results['job_data'] = {'id': job_id, 'clientContact_id': contact_id}

    note_data_v4 = {
        'action': 'Job Update',
        'comments': f'[DIAG-V4] personReference=ClientContact — {datetime.utcnow().isoformat()}',
        'isDeleted': False,
        'commentingPerson': {'id': int(bh.user_id)},
    }
    if contact_id:
        note_data_v4['personReference'] = {'id': int(contact_id)}
    else:
        note_data_v4['personReference'] = {'id': int(bh.user_id)}
    r4 = bh.session.put(f"{bh.base_url}entity/Note", params=params, json=note_data_v4, timeout=30)
    r4_data = r4.json() if r4.text else {}
    results['v4_clientcontact_ref'] = {'status': r4.status_code, 'body': r4_data}
    v4_note_id = r4_data.get('changedEntityId')
    if v4_note_id:
        ne_data = {'note': {'id': int(v4_note_id)}, 'targetEntityID': int(job_id), 'targetEntityName': 'JobOrder'}
        r4b = bh.session.put(f"{bh.base_url}entity/NoteEntity", params=params, json=ne_data, timeout=30)
        results['v4_noteentity_link'] = {'status': r4b.status_code, 'body': r4b.json() if r4b.text else {}}

    note_data_v5 = {
        'action': 'Job Update',
        'comments': f'[DIAG-V5] personReference=ClientContact + clientContacts array — {datetime.utcnow().isoformat()}',
        'isDeleted': False,
        'commentingPerson': {'id': int(bh.user_id)},
    }
    if contact_id:
        note_data_v5['personReference'] = {'id': int(contact_id)}
        note_data_v5['clientContacts'] = [{'id': int(contact_id)}]
    else:
        note_data_v5['personReference'] = {'id': int(bh.user_id)}
    r5 = bh.session.put(f"{bh.base_url}entity/Note", params=params, json=note_data_v5, timeout=30)
    r5_data = r5.json() if r5.text else {}
    results['v5_clientcontact_plus_array'] = {'status': r5.status_code, 'body': r5_data}
    v5_note_id = r5_data.get('changedEntityId')
    if v5_note_id:
        ne_data = {'note': {'id': int(v5_note_id)}, 'targetEntityID': int(job_id), 'targetEntityName': 'JobOrder'}
        r5b = bh.session.put(f"{bh.base_url}entity/NoteEntity", params=params, json=ne_data, timeout=30)
        results['v5_noteentity_link'] = {'status': r5b.status_code, 'body': r5b.json() if r5b.text else {}}

    return jsonify(results)
