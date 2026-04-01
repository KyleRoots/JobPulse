import csv
import io
import json
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from flask import Blueprint, render_template, jsonify, request, redirect, url_for, Response, send_file
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

    ai_understanding = ticket.parsed_ai_understanding
    if ai_understanding is None and ticket.ai_understanding:
        ai_understanding = {'understanding': ticket.ai_understanding}

    from scout_support_service import PLATFORM_CATEGORIES
    is_platform = ticket.category in PLATFORM_CATEGORIES

    attachments = ticket.attachments.all()

    return render_template('scout_support_ticket.html',
                           ticket=ticket,
                           conversations=conversations,
                           actions=actions,
                           ai_understanding=ai_understanding,
                           is_platform=is_platform,
                           attachments=attachments,
                           active_page='scout_support')


@scout_support_bp.route('/scout-support/attachment/<int:attachment_id>')
@login_required
def serve_attachment(attachment_id):
    from models import SupportAttachment, SupportTicket

    attachment = SupportAttachment.query.get_or_404(attachment_id)
    ticket = SupportTicket.query.get(attachment.ticket_id)

    if not current_user.is_admin and (not ticket or ticket.submitter_email != current_user.email):
        return redirect(url_for('scout_support.scout_support_dashboard'))

    SAFE_INLINE_TYPES = {
        'image/png', 'image/jpeg', 'image/gif', 'image/webp',
        'application/pdf',
    }
    serve_inline = attachment.content_type in SAFE_INLINE_TYPES

    response = send_file(
        io.BytesIO(attachment.file_data),
        mimetype=attachment.content_type,
        as_attachment=not serve_inline,
        download_name=attachment.filename,
    )
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


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


@scout_support_bp.route('/api/scout-support/reopen/<ticket_number>', methods=['POST'])
@login_required
def api_reopen_ticket(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    svc = ScoutSupportService()
    success = svc.reopen_ticket(ticket.id, current_user.email)

    return jsonify({'success': success})


@scout_support_bp.route('/api/scout-support/platform-status/<ticket_number>', methods=['POST'])
@login_required
def api_update_platform_status(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService, PLATFORM_CATEGORIES

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    if ticket.category not in PLATFORM_CATEGORIES:
        return jsonify({'error': 'This endpoint is for platform tickets only'}), 400

    data = request.json or {}
    new_status = data.get('status', '')

    if not new_status:
        return jsonify({'error': 'Status is required'}), 400

    svc = ScoutSupportService()
    success = svc.update_platform_ticket_status(ticket.id, new_status, current_user.email)

    if not success:
        return jsonify({'error': 'Invalid status transition'}), 400

    return jsonify({'success': True, 'new_status': new_status})


@scout_support_bp.route('/api/scout-support/platform-close/<ticket_number>', methods=['POST'])
@login_required
def api_close_platform_ticket(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService, PLATFORM_CATEGORIES

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    if ticket.category not in PLATFORM_CATEGORIES:
        return jsonify({'error': 'This endpoint is for platform tickets only'}), 400

    data = request.json or {}
    resolution_note = data.get('resolution_note', '').strip()

    if not resolution_note:
        return jsonify({'error': 'A resolution note is required'}), 400

    svc = ScoutSupportService()
    success = svc.close_platform_ticket(ticket.id, resolution_note, current_user.email)

    return jsonify({'success': success})


@scout_support_bp.route('/api/scout-support/reply/<ticket_number>', methods=['POST'])
@login_required
def api_reply_to_ticket(ticket_number):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket
    from scout_support_service import ScoutSupportService, PLATFORM_CATEGORIES

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    if ticket.status in ('completed', 'closed'):
        return jsonify({'error': 'Cannot reply to a resolved ticket'}), 400

    data = request.json or {}
    reply_body = data.get('reply', '').strip()

    if not reply_body:
        return jsonify({'error': 'Reply message is required'}), 400

    svc = ScoutSupportService()
    if ticket.category in PLATFORM_CATEGORIES:
        success = svc.reply_to_platform_ticket(ticket.id, reply_body, current_user.email)
    else:
        success = svc.reply_to_ticket(ticket.id, reply_body, current_user.email)

    return jsonify({'success': success})


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


def _compute_analytics(days=90):
    from models import SupportTicket, SupportConversation
    from sqlalchemy import func

    cutoff = datetime.utcnow() - timedelta(days=days)
    all_tickets = SupportTicket.query.filter(SupportTicket.created_at >= cutoff).all()
    total = len(all_tickets)

    status_counts = Counter(t.status for t in all_tickets)
    category_counts = Counter(t.category for t in all_tickets)
    priority_counts = Counter(t.priority for t in all_tickets)
    brand_counts = Counter(t.brand for t in all_tickets)

    resolved = [t for t in all_tickets if t.resolved_at and t.created_at]
    resolution_times = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in resolved]
    avg_resolution_hrs = round(sum(resolution_times) / len(resolution_times), 1) if resolution_times else 0
    median_resolution_hrs = round(sorted(resolution_times)[len(resolution_times) // 2], 1) if resolution_times else 0

    escalated = sum(1 for t in all_tickets if t.status == 'escalated' or t.escalation_reason)
    escalation_rate = round((escalated / total) * 100, 1) if total else 0

    first_contact_resolved = 0
    for t in resolved:
        clarify_convos = SupportConversation.query.filter_by(
            ticket_id=t.id, email_type='clarification'
        ).count()
        if clarify_convos == 0:
            first_contact_resolved += 1
    fcr_rate = round((first_contact_resolved / len(resolved)) * 100, 1) if resolved else 0

    clarification_counts = []
    for t in all_tickets:
        c_count = SupportConversation.query.filter_by(
            ticket_id=t.id, email_type='clarification'
        ).count()
        clarification_counts.append(c_count)
    avg_clarifications = round(sum(clarification_counts) / len(clarification_counts), 1) if clarification_counts else 0

    weekly_data = defaultdict(int)
    for t in all_tickets:
        week_start = t.created_at - timedelta(days=t.created_at.weekday())
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_data[week_key] += 1

    sorted_weeks = sorted(weekly_data.keys())
    volume_labels = [datetime.strptime(w, '%Y-%m-%d').strftime('%b %d') for w in sorted_weeks]
    volume_values = [weekly_data[w] for w in sorted_weeks]

    status_labels_map = {
        'new': 'New', 'acknowledged': 'Acknowledged', 'clarifying': 'Clarifying',
        'solution_proposed': 'Solution Proposed', 'awaiting_user_approval': 'User Approval',
        'awaiting_admin_approval': 'Admin Approval', 'approved': 'Approved',
        'executing': 'Executing', 'completed': 'Completed', 'execution_failed': 'Failed',
        'on_hold': 'On Hold', 'closed': 'Closed', 'escalated': 'Escalated',
    }
    category_labels_map = {
        'ats_issue': 'ATS Issue', 'data_correction': 'Data Correction',
        'candidate_parsing': 'Candidate Parsing', 'job_posting': 'Job Posting',
        'account_access': 'Account Access', 'email_notifications': 'Email/Notifications',
        'feature_request': 'Feature Request', 'other': 'Other',
        'backoffice_onboarding': 'Back-Office: Onboarding',
        'backoffice_finance': 'Back-Office: Finance',
        'platform_bug': 'Platform Bug', 'platform_feature': 'Platform Feature',
        'platform_question': 'Platform Question', 'platform_other': 'Platform Feedback',
    }

    return {
        'total': total,
        'days': days,
        'status_labels': [status_labels_map.get(s, s) for s in status_counts.keys()],
        'status_values': list(status_counts.values()),
        'category_labels': [category_labels_map.get(c, c) for c in category_counts.keys()],
        'category_values': list(category_counts.values()),
        'priority_labels': [p.capitalize() for p in priority_counts.keys()],
        'priority_values': list(priority_counts.values()),
        'brand_labels': list(brand_counts.keys()),
        'brand_values': list(brand_counts.values()),
        'avg_resolution_hrs': avg_resolution_hrs,
        'median_resolution_hrs': median_resolution_hrs,
        'escalation_rate': escalation_rate,
        'escalated_count': escalated,
        'fcr_rate': fcr_rate,
        'first_contact_resolved': first_contact_resolved,
        'resolved_count': len(resolved),
        'avg_clarifications': avg_clarifications,
        'volume_labels': volume_labels,
        'volume_values': volume_values,
        'tickets': all_tickets,
    }


@scout_support_bp.route('/scout-support/analytics')
@login_required
def analytics_dashboard():
    if not current_user.is_admin:
        return redirect(url_for('scout_support.scout_support_dashboard'))

    days = request.args.get('days', 90, type=int)
    if days not in (30, 60, 90, 180, 365):
        days = 90

    analytics = _compute_analytics(days)
    analytics.pop('tickets', None)

    return render_template('scout_support_analytics.html',
                           analytics=analytics,
                           selected_days=days,
                           active_page='scout_support')


@scout_support_bp.route('/api/scout-support/analytics')
@login_required
def api_analytics():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    days = request.args.get('days', 90, type=int)
    if days not in (30, 60, 90, 180, 365):
        days = 90

    analytics = _compute_analytics(days)
    analytics.pop('tickets', None)
    return jsonify(analytics)


@scout_support_bp.route('/scout-support/analytics/export')
@login_required
def analytics_export():
    if not current_user.is_admin:
        return redirect(url_for('scout_support.scout_support_dashboard'))

    days = request.args.get('days', 90, type=int)
    if days not in (30, 60, 90, 180, 365):
        days = 90

    analytics = _compute_analytics(days)
    tickets = analytics.pop('tickets', [])

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['Scout Support Analytics Report'])
    writer.writerow([f'Period: Last {days} days', f'Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}'])
    writer.writerow([])

    writer.writerow(['Summary Metrics'])
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Total Tickets', analytics['total']])
    writer.writerow(['Avg Resolution Time (hrs)', analytics['avg_resolution_hrs']])
    writer.writerow(['Median Resolution Time (hrs)', analytics['median_resolution_hrs']])
    writer.writerow(['First-Contact Resolution Rate', f"{analytics['fcr_rate']}%"])
    writer.writerow(['Escalation Rate', f"{analytics['escalation_rate']}%"])
    writer.writerow(['Avg Clarification Loops', analytics['avg_clarifications']])
    writer.writerow([])

    writer.writerow(['Status Distribution'])
    writer.writerow(['Status', 'Count'])
    for label, val in zip(analytics['status_labels'], analytics['status_values']):
        writer.writerow([label, val])
    writer.writerow([])

    writer.writerow(['Category Distribution'])
    writer.writerow(['Category', 'Count'])
    for label, val in zip(analytics['category_labels'], analytics['category_values']):
        writer.writerow([label, val])
    writer.writerow([])

    writer.writerow(['Weekly Volume'])
    writer.writerow(['Week Starting', 'Tickets'])
    for label, val in zip(analytics['volume_labels'], analytics['volume_values']):
        writer.writerow([label, val])
    writer.writerow([])

    writer.writerow(['Ticket Details'])
    writer.writerow(['Ticket #', 'Subject', 'Category', 'Priority', 'Status', 'Brand',
                     'Submitter', 'Created', 'Resolved', 'Resolution (hrs)'])
    for t in tickets:
        res_hrs = ''
        if t.resolved_at and t.created_at:
            res_hrs = round((t.resolved_at - t.created_at).total_seconds() / 3600, 1)
        writer.writerow([
            t.ticket_number, t.subject, t.category, t.priority, t.status, t.brand,
            t.submitter_name,
            t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
            t.resolved_at.strftime('%Y-%m-%d %H:%M') if t.resolved_at else '',
            res_hrs,
        ])

    output.seek(0)
    filename = f'scout_support_analytics_{days}d_{datetime.utcnow().strftime("%Y%m%d")}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


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
