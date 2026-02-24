import logging
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from extensions import db

logger = logging.getLogger(__name__)
email_logs_bp = Blueprint('email_logs', __name__)


@email_logs_bp.route('/email-logs')
@login_required
def email_logs_page():
    """Display email delivery logs"""
    from models import EmailDeliveryLog

    page = request.args.get('page', 1, type=int)
    per_page = 50
    notification_type = request.args.get('type')

    query = EmailDeliveryLog.query
    if notification_type:
        query = query.filter(EmailDeliveryLog.notification_type == notification_type)

    logs = query.order_by(EmailDeliveryLog.sent_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    return render_template('email_logs.html', logs=logs, active_page='email_logs')


@email_logs_bp.route('/api/email-logs')
@login_required
def api_email_logs():
    """API endpoint for getting paginated email delivery logs"""
    from models import EmailDeliveryLog

    page = request.args.get('page', 1, type=int)
    per_page = 50
    notification_type = request.args.get('type')

    query = EmailDeliveryLog.query
    if notification_type:
        query = query.filter(EmailDeliveryLog.notification_type == notification_type)

    logs = query.order_by(EmailDeliveryLog.sent_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    return jsonify({
        'logs': [{
            'id': log.id,
            'notification_type': log.notification_type,
            'job_id': log.job_id,
            'job_title': log.job_title,
            'recipient_email': log.recipient_email,
            'delivery_status': log.delivery_status,
            'sendgrid_message_id': log.sendgrid_message_id,
            'error_message': log.error_message,
            'schedule_name': log.schedule_name,
            'changes_summary': log.changes_summary,
            'sent_at': log.sent_at.strftime('%Y-%m-%d %H:%M:%S')
        } for log in logs.items],
        'pagination': {
            'page': logs.page,
            'pages': logs.pages,
            'total': logs.total,
            'has_next': logs.has_next,
            'has_prev': logs.has_prev
        }
    })
