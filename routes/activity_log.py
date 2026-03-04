import json
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request
from flask_login import login_required
from routes import register_admin_guard
from extensions import db

logger = logging.getLogger(__name__)
activity_log_bp = Blueprint('activity_log', __name__)
register_admin_guard(activity_log_bp)


def _parse_user_agent(ua_string):
    if not ua_string:
        return 'Unknown'
    ua = ua_string.lower()
    browser = 'Unknown'
    if 'edg/' in ua or 'edge/' in ua:
        browser = 'Edge'
    elif 'chrome/' in ua and 'safari/' in ua:
        browser = 'Chrome'
    elif 'firefox/' in ua:
        browser = 'Firefox'
    elif 'safari/' in ua:
        browser = 'Safari'
    elif 'opera/' in ua or 'opr/' in ua:
        browser = 'Opera'

    os_name = 'Unknown'
    if 'windows' in ua:
        os_name = 'Windows'
    elif 'macintosh' in ua or 'mac os' in ua:
        os_name = 'macOS'
    elif 'linux' in ua:
        os_name = 'Linux'
    elif 'iphone' in ua or 'ipad' in ua:
        os_name = 'iOS'
    elif 'android' in ua:
        os_name = 'Android'

    return f'{browser} / {os_name}'


@activity_log_bp.route('/activity-log')
@login_required
def activity_log_page():
    from models import UserActivityLog, EmailDeliveryLog, User

    tab = request.args.get('tab', 'logins')
    user_filter = request.args.get('user', type=int)
    days = request.args.get('days', 30, type=int)
    page = request.args.get('page', 1, type=int)
    per_page = 50
    email_type = request.args.get('email_type', '').strip()
    email_status = request.args.get('email_status', '').strip()
    recipient_search = request.args.get('recipient_search', '').strip()

    cutoff = datetime.utcnow() - timedelta(days=days)
    users = User.query.order_by(User.username).all()

    logins_data = None
    modules_data = None
    emails_data = None

    if tab == 'logins':
        q = db.session.query(UserActivityLog, User).join(User, UserActivityLog.user_id == User.id).filter(
            UserActivityLog.activity_type == 'login',
            UserActivityLog.created_at >= cutoff
        )
        if user_filter:
            q = q.filter(UserActivityLog.user_id == user_filter)
        logins_data = q.order_by(UserActivityLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    elif tab == 'modules':
        q = db.session.query(UserActivityLog, User).join(User, UserActivityLog.user_id == User.id).filter(
            UserActivityLog.activity_type == 'module_access',
            UserActivityLog.created_at >= cutoff
        )
        if user_filter:
            q = q.filter(UserActivityLog.user_id == user_filter)
        modules_data = q.order_by(UserActivityLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    elif tab == 'emails':
        allowed_types = ['welcome_email', 'password_reset_email', 'vetting_recruiter_notification']
        if email_type and email_type in allowed_types:
            q = EmailDeliveryLog.query.filter(
                EmailDeliveryLog.notification_type == email_type,
                EmailDeliveryLog.sent_at >= cutoff
            )
        else:
            q = EmailDeliveryLog.query.filter(
                EmailDeliveryLog.notification_type.in_(allowed_types),
                EmailDeliveryLog.sent_at >= cutoff
            )
        if email_status and email_status in ('sent', 'failed'):
            q = q.filter(EmailDeliveryLog.delivery_status == email_status)
        if recipient_search:
            q = q.filter(EmailDeliveryLog.recipient_email.ilike(f'%{recipient_search}%'))
        if user_filter:
            target_user = User.query.get(user_filter)
            if target_user:
                q = q.filter(EmailDeliveryLog.recipient_email == target_user.email)
        emails_data = q.order_by(EmailDeliveryLog.sent_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    total_logins_7d = UserActivityLog.query.filter(
        UserActivityLog.activity_type == 'login',
        UserActivityLog.created_at >= seven_days_ago
    ).count()
    active_users_7d = db.session.query(db.func.count(db.func.distinct(UserActivityLog.user_id))).filter(
        UserActivityLog.created_at >= seven_days_ago
    ).scalar() or 0
    email_types_count = ['welcome_email', 'password_reset_email', 'vetting_recruiter_notification']
    emails_sent_7d = EmailDeliveryLog.query.filter(
        EmailDeliveryLog.notification_type.in_(email_types_count),
        EmailDeliveryLog.sent_at >= seven_days_ago
    ).count()

    return render_template('activity_log.html',
                           active_page='activity_log',
                           tab=tab,
                           user_filter=user_filter,
                           days=days,
                           page=page,
                           users=users,
                           logins_data=logins_data,
                           modules_data=modules_data,
                           emails_data=emails_data,
                           total_logins_7d=total_logins_7d,
                           active_users_7d=active_users_7d,
                           emails_sent_7d=emails_sent_7d,
                           email_type=email_type,
                           email_status=email_status,
                           recipient_search=recipient_search,
                           parse_user_agent=_parse_user_agent,
                           json=json)
