import io
import json
import logging
from flask import Blueprint, render_template, redirect, url_for, jsonify, request, send_file
from flask_login import login_required, current_user
from extensions import db

platform_support_bp = Blueprint('platform_support', __name__)
logger = logging.getLogger(__name__)


@platform_support_bp.route('/my-tickets')
@login_required
def my_tickets():
    from models import SupportTicket
    from scout_support_service import CATEGORY_LABELS, PLATFORM_CATEGORIES

    all_user_tickets = SupportTicket.query.filter_by(
        submitter_email=current_user.email
    ).order_by(SupportTicket.created_at.desc()).all()

    has_support_module = current_user.is_admin or current_user.has_module('scout_support')

    if has_support_module:
        tickets = all_user_tickets
    else:
        tickets = [t for t in all_user_tickets if t.category in PLATFORM_CATEGORIES]

    platform_tickets = [t for t in tickets if t.category in PLATFORM_CATEGORIES]
    ats_tickets = [t for t in tickets if t.category not in PLATFORM_CATEGORIES]

    stats = {
        'total': len(tickets),
        'platform': len(platform_tickets),
        'ats': len(ats_tickets),
        'open': sum(1 for t in tickets if t.status not in ('completed', 'closed')),
    }

    return render_template('my_tickets.html',
                           tickets=tickets,
                           stats=stats,
                           category_labels=CATEGORY_LABELS,
                           platform_categories=PLATFORM_CATEGORIES,
                           has_support_module=has_support_module,
                           active_page='my_tickets')


@platform_support_bp.route('/my-tickets/<ticket_number>')
@login_required
def my_ticket_detail(ticket_number):
    from models import SupportTicket
    from scout_support_service import PLATFORM_CATEGORIES

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()

    if ticket.submitter_email != current_user.email and not current_user.is_admin:
        return redirect(url_for('platform_support.my_tickets'))

    has_support_module = current_user.is_admin or current_user.has_module('scout_support')
    if not has_support_module and ticket.category not in PLATFORM_CATEGORIES:
        return redirect(url_for('platform_support.my_tickets'))

    from models import SupportConversation
    ADMIN_ONLY_TYPES = {
        'admin_approval_request', 'admin_reply', 'admin_clarification_response',
        'admin_new_ticket_notification', 'completion_admin',
        'admin_execution_failure',
        'admin_ai_draft', 'admin_ai_draft_email', 'admin_ai_instruction',
        'stakeholder_new_ticket', 'stakeholder_completed',
        'stakeholder_escalated', 'stakeholder_status_update',
        'escalation_admin_summary',
    }
    all_conversations = ticket.conversations.order_by(db.text('created_at ASC')).all()
    if current_user.is_admin:
        conversations = all_conversations
    else:
        conversations = [c for c in all_conversations if c.email_type not in ADMIN_ONLY_TYPES]
    attachments = ticket.attachments.all()

    ai_understanding = None
    if ticket.ai_understanding:
        try:
            ai_understanding = json.loads(ticket.ai_understanding)
        except (json.JSONDecodeError, TypeError):
            ai_understanding = {'understanding': ticket.ai_understanding}

    return render_template('my_ticket_detail.html',
                           ticket=ticket,
                           conversations=conversations,
                           attachments=attachments,
                           ai_understanding=ai_understanding,
                           active_page='my_tickets')


@platform_support_bp.route('/my-tickets/attachment/<int:attachment_id>')
@login_required
def serve_my_attachment(attachment_id):
    from models import SupportAttachment, SupportTicket

    attachment = SupportAttachment.query.get_or_404(attachment_id)
    ticket = SupportTicket.query.get(attachment.ticket_id)

    if not ticket or (ticket.submitter_email != current_user.email and not current_user.is_admin):
        return redirect(url_for('platform_support.my_tickets'))

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


@platform_support_bp.route('/api/feedback', methods=['POST'])
@login_required
def api_submit_feedback():
    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid request data"}), 400
        feedback_type = data.get('type', 'other')
        message = data.get('message', '')
        page = data.get('page', 'Unknown')

        if not message or not message.strip():
            return jsonify({"success": False, "error": "Message is required"}), 400

        category_map = {
            'bug': 'platform_bug',
            'feature': 'platform_feature',
            'question': 'platform_question',
            'other': 'platform_other',
        }
        category = category_map.get(feedback_type, 'platform_other')

        type_labels = {
            'feature': 'Feature Request',
            'bug': 'Bug Report',
            'question': 'Question',
            'other': 'Feedback',
        }
        type_label = type_labels.get(feedback_type, 'Feedback')

        subject = f"{type_label}: {message[:80]}{'...' if len(message) > 80 else ''}"
        description = f"{message}\n\nPage: {page}"

        user_company = getattr(current_user, 'company', None) or 'Myticas'
        brand = 'STSI' if user_company and 'stsi' in user_company.lower() else 'Myticas'

        from scout_support_service import ScoutSupportService
        svc = ScoutSupportService()
        ticket = svc.create_ticket(
            category=category,
            subject=subject,
            description=description,
            submitter_name=current_user.full_name if hasattr(current_user, 'full_name') and current_user.full_name else current_user.username,
            submitter_email=current_user.email,
            submitter_department=getattr(current_user, 'department', None),
            brand=brand,
            priority='medium' if feedback_type == 'bug' else 'low',
        )

        try:
            svc.process_new_ticket(ticket.id)
        except Exception as proc_err:
            logger.error(f"Platform ticket AI processing failed for {ticket.ticket_number}: {proc_err}")

        logger.info(f"Platform feedback ticket created: {ticket.ticket_number} by {current_user.email}")

        return jsonify({
            "success": True,
            "message": "Your feedback has been submitted as a support ticket.",
            "ticket_number": ticket.ticket_number,
        })

    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
