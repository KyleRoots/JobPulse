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

    note_data_v1 = {
        'action': 'Job Update',
        'comments': f'[DIAG-V1] Test note with jobOrders array — {datetime.utcnow().isoformat()}',
        'isDeleted': False,
        'personReference': {'id': int(bh.user_id)},
        'commentingPerson': {'id': int(bh.user_id)},
        'jobOrders': [{'id': int(job_id)}],
    }
    r1 = bh.session.put(f"{bh.base_url}entity/Note", params=params, json=note_data_v1, timeout=30)
    results['v1_jobOrders_array'] = {'status': r1.status_code, 'body': r1.json() if r1.text else {}}

    note_data_v2 = {
        'action': 'Job Update',
        'comments': f'[DIAG-V2] Test note — NoteEntity link only — {datetime.utcnow().isoformat()}',
        'isDeleted': False,
        'personReference': {'id': int(bh.user_id)},
        'commentingPerson': {'id': int(bh.user_id)},
    }
    r2 = bh.session.put(f"{bh.base_url}entity/Note", params=params, json=note_data_v2, timeout=30)
    r2_data = r2.json() if r2.text else {}
    results['v2_noteentity_only'] = {'status': r2.status_code, 'body': r2_data}
    v2_note_id = r2_data.get('changedEntityId')
    if v2_note_id:
        ne_data = {'note': {'id': int(v2_note_id)}, 'targetEntityID': int(job_id), 'targetEntityName': 'JobOrder'}
        r2b = bh.session.put(f"{bh.base_url}entity/NoteEntity", params=params, json=ne_data, timeout=30)
        results['v2_noteentity_link'] = {'status': r2b.status_code, 'body': r2b.json() if r2b.text else {}}

    note_data_v3 = {
        'action': 'Job Update',
        'comments': f'[DIAG-V3] Test note — jobOrders + NoteEntity — {datetime.utcnow().isoformat()}',
        'isDeleted': False,
        'personReference': {'id': int(bh.user_id)},
        'commentingPerson': {'id': int(bh.user_id)},
        'jobOrders': [{'id': int(job_id)}],
    }
    r3 = bh.session.put(f"{bh.base_url}entity/Note", params=params, json=note_data_v3, timeout=30)
    r3_data = r3.json() if r3.text else {}
    results['v3_both'] = {'status': r3.status_code, 'body': r3_data}
    v3_note_id = r3_data.get('changedEntityId')
    if v3_note_id:
        ne_data = {'note': {'id': int(v3_note_id)}, 'targetEntityID': int(job_id), 'targetEntityName': 'JobOrder'}
        r3b = bh.session.put(f"{bh.base_url}entity/NoteEntity", params=params, json=ne_data, timeout=30)
        results['v3_noteentity_link'] = {'status': r3b.status_code, 'body': r3b.json() if r3b.text else {}}

    return jsonify(results)
