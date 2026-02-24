import os
import logging
import base64
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, make_response, session
from extensions import csrf, db
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy import or_, func
import time

logger = logging.getLogger(__name__)
support_request_bp = Blueprint('support_request', __name__)

CATEGORY_LABELS = {
    'ats_issue': 'ATS / Bullhorn Issue',
    'candidate_parsing': 'Candidate Parsing Error',
    'job_posting': 'Job Posting Issue',
    'account_access': 'Account / Access Request',
    'email_notifications': 'Email / Notification Issue',
    'data_correction': 'Data Correction Request',
    'feature_request': 'Feature Request',
    'other': 'Other',
    'backoffice_onboarding': 'Back-Office: Onboarding',
    'backoffice_finance': 'Back-Office: Finance (BTE)',
}

CATEGORY_ICONS = {
    'ats_issue': '🔧',
    'candidate_parsing': '⚠️',
    'job_posting': '📋',
    'account_access': '🔑',
    'email_notifications': '📧',
    'data_correction': '📝',
    'feature_request': '💡',
    'other': '📌',
    'backoffice_onboarding': '📁',
    'backoffice_finance': '💼',
}

PRIORITY_LABELS = {
    'low': 'Low',
    'medium': 'Medium',
    'high': 'High',
}

PRIORITY_COLORS = {
    'low': '#28a745',
    'medium': '#ffc107',
    'high': '#dc3545',
}

_DAN_SIFER_EMAIL = 'dsifer@myticas.com'
_ANITA_BARKER_EMAIL = 'abarker@myticas.com'
_ANASTASIYA_IVANOVA_EMAIL = 'ai@myticas.com'
_TECH_SUPPORT_EMAIL = 'techsupport@myticas.com'
_CC_ALWAYS = 'kroots@myticas.com'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xlsx', 'csv', 'txt'}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_FILES = 5

_rate_limit_store = {}
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 5


_MYTICAS_FINANCE_EMAIL = 'accounting@myticas.com'

def get_routing_info(category, department=''):
    cc = [_CC_ALWAYS]
    dept = (department or '').strip()

    if category == 'email_notifications':
        return _TECH_SUPPORT_EMAIL, cc

    if category == 'backoffice_onboarding':
        if dept == 'MYT-Ottawa':
            return _ANITA_BARKER_EMAIL, cc
        return _ANASTASIYA_IVANOVA_EMAIL, cc

    if category == 'backoffice_finance':
        if dept == 'MYT-Ottawa':
            return _MYTICAS_FINANCE_EMAIL, cc
        return _ANASTASIYA_IVANOVA_EMAIL, cc

    return _DAN_SIFER_EMAIL, cc


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def check_rate_limit(ip):
    now = time.time()
    if ip in _rate_limit_store:
        timestamps = [t for t in _rate_limit_store[ip] if now - t < RATE_LIMIT_WINDOW]
        _rate_limit_store[ip] = timestamps
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        _rate_limit_store[ip].append(now)
    else:
        _rate_limit_store[ip] = [now]
    return True


def _serve_support_form():
    response = make_response(render_template('support_request.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@support_request_bp.route('/support')
@csrf.exempt
def support_form():
    return _serve_support_form()


@support_request_bp.before_app_request
def redirect_support_domain():
    host = request.host.lower()
    if 'support.myticas.com' in host:
        path = request.path
        if path == '/' or path == '':
            return _serve_support_form()
        if path.startswith('/static/') or path.startswith('/support'):
            return None
        from flask import abort
        abort(404)


@support_request_bp.route('/support/contacts/search')
@csrf.exempt
def search_support_contacts():
    query = request.args.get('q', '').strip()
    brand = request.args.get('brand', 'Myticas').strip()
    debug = request.args.get('debug', '') == '1'
    if len(query) < 1:
        if debug:
            return jsonify({'version': 'v2', 'msg': 'empty query'})
        return jsonify([])
    try:
        from models import SupportContact
        q_lower = query.lower()

        if debug:
            raw_count = db.session.execute(db.text("SELECT COUNT(*) FROM support_contact")).scalar()
            raw = db.session.execute(
                db.text("SELECT id, first_name, last_name, email, brand, is_active FROM support_contact LIMIT 5")
            ).fetchall()
            return jsonify({
                'version': 'v2',
                'debug': True,
                'raw_count': raw_count,
                'sample': [{'id': r[0], 'first_name': r[1], 'last_name': r[2], 'email': r[3], 'brand': r[4], 'is_active': r[5]} for r in raw],
                'query': q_lower,
                'brand': brand
            })

        contacts = SupportContact.query.filter(
            SupportContact.is_active == True,
            SupportContact.brand == brand,
            or_(
                func.lower(SupportContact.first_name).like(f'{q_lower}%'),
                func.lower(SupportContact.last_name).like(f'{q_lower}%'),
                func.lower(SupportContact.first_name + ' ' + SupportContact.last_name).like(f'{q_lower}%'),
            )
        ).order_by(SupportContact.first_name, SupportContact.last_name).limit(10).all()
        return jsonify([c.to_dict() for c in contacts])
    except Exception as e:
        logger.error(f"Error searching support contacts: {e}", exc_info=True)
        return jsonify({'version': 'v2', 'error': str(e), 'type': type(e).__name__}), 500


@support_request_bp.route('/support/send-test-email')
@csrf.exempt
def send_test_email():
    html_content = build_support_email_html(
        requester_name='Innocent Nangoma',
        requester_email='inangoma@myticas.com',
        internal_department='MYT-Ottawa',
        category_label='Back-Office: Onboarding',
        category_icon='📁',
        priority_label='Medium',
        priority_color='#ffc107',
        subject='Test Support Request — Please Ignore',
        description='This is a test email to preview the updated support request template. It demonstrates the new email layout including the Internal Department field and routing logic.',
        attachments=[]
    )
    success = send_support_email(
        to_email='kroots@myticas.com',
        cc_emails=[],
        reply_to_email='inangoma@myticas.com',
        subject='[TEST] Support Request Template Preview — Back-Office: Onboarding',
        html_content=html_content,
        attachments=[]
    )
    if success:
        return jsonify({'success': True, 'message': 'Test email sent to kroots@myticas.com'})
    else:
        return jsonify({'success': False, 'error': 'Failed to send test email'}), 500


@support_request_bp.route('/support/test/')
@csrf.exempt
def support_form_test():
    try:
        from models import SupportContact
        raw_count = db.session.execute(db.text("SELECT COUNT(*) FROM support_contact")).scalar()
        contact_count = SupportContact.query.filter_by(brand='Myticas', is_active=True).count()
        all_contacts = SupportContact.query.limit(5).all()
        sample = [c.to_dict() for c in all_contacts]
        test_query = SupportContact.query.filter(
            SupportContact.is_active == True,
            SupportContact.brand == 'Myticas',
            func.lower(SupportContact.first_name).like('ky%')
        ).all()
        test_results = [c.to_dict() for c in test_query]
        return jsonify({
            'status': 'ok',
            'raw_sql_count': raw_count,
            'orm_count': contact_count,
            'sample_5': sample,
            'test_search_ky': test_results,
            'table_exists': True,
            'db_url_prefix': os.environ.get('DATABASE_URL', '')[:30]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e), 'type': type(e).__name__}), 500


@support_request_bp.route('/support/submit', methods=['POST'])
@csrf.exempt
def submit_support_request():
    try:
        honeypot = request.form.get('website_url', '').strip()
        if honeypot:
            logger.warning(f"Honeypot triggered from IP {request.remote_addr}")
            return jsonify({'success': True, 'message': 'Support request submitted successfully.'})

        if not check_rate_limit(request.remote_addr):
            return jsonify({'success': False, 'error': 'Too many requests. Please wait a moment before trying again.'}), 429

        requester_name = request.form.get('requesterName', '').strip()
        requester_email = request.form.get('requesterEmail', '').strip()
        category = request.form.get('issueCategory', '').strip()
        priority = request.form.get('priority', '').strip()
        subject = request.form.get('subject', '').strip()
        description = request.form.get('description', '').strip()
        internal_department = request.form.get('internalDepartment', '').strip()

        if not all([requester_name, requester_email, category, priority, subject, description]):
            return jsonify({'success': False, 'error': 'All required fields must be filled in.'}), 400

        attachments = request.files.getlist('attachments')
        valid_attachments = []
        for file in attachments:
            if file and file.filename and allowed_file(file.filename):
                if len(valid_attachments) >= MAX_FILES:
                    break
                file_data = file.read()
                if len(file_data) <= MAX_FILE_SIZE:
                    safe_name = secure_filename(file.filename) or 'attachment'
                    valid_attachments.append({
                        'filename': safe_name,
                        'data': file_data,
                        'content_type': file.content_type or 'application/octet-stream'
                    })

        route_email, cc_emails = get_routing_info(category, internal_department)
        category_label = CATEGORY_LABELS.get(category, category)
        category_icon = CATEGORY_ICONS.get(category, '📌')
        priority_label = PRIORITY_LABELS.get(priority, priority)
        priority_color = PRIORITY_COLORS.get(priority, '#ffc107')

        html_content = build_support_email_html(
            requester_name=requester_name,
            requester_email=requester_email,
            internal_department=internal_department,
            category_label=category_label,
            category_icon=category_icon,
            priority_label=priority_label,
            priority_color=priority_color,
            subject=subject,
            description=description,
            attachments=valid_attachments
        )

        email_subject = f"[Support Request] {category_icon} {category_label} — {subject}"

        success = send_support_email(
            to_email=route_email,
            cc_emails=cc_emails,
            reply_to_email=requester_email,
            subject=email_subject,
            html_content=html_content,
            attachments=valid_attachments
        )

        if success:
            logger.info(f"Support request submitted by {requester_name} ({requester_email}) — Category: {category_label}, Priority: {priority_label}")
            return jsonify({'success': True, 'message': 'Support request submitted successfully.'})
        else:
            return jsonify({'success': False, 'error': 'Failed to send support request. Please try again.'}), 500

    except Exception as e:
        logger.error(f"Error submitting support request: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'An unexpected error occurred. Please try again.'}), 500


def build_support_email_html(requester_name, requester_email, internal_department, category_label, category_icon,
                              priority_label, priority_color, subject, description, attachments):
    attachment_section = ''
    if attachments:
        file_list = ''.join(
            f'<li style="padding: 4px 0; color: #334155;">'
            f'📎 {att["filename"]} ({len(att["data"]) / 1024:.1f} KB)</li>'
            for att in attachments
        )
        attachment_section = f'''
        <div style="margin-top: 16px; border-top: 1px solid #e2e8f0; padding-top: 16px;">
            <div style="color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 8px;">Attachments ({len(attachments)})</div>
            <ul style="margin: 0; padding-left: 20px; list-style: none;">
                {file_list}
            </ul>
        </div>
        '''

    description_html = description.replace('\n', '<br>')

    return f'''
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 620px; margin: 0 auto;">
        <div style="background: #1e3c72; padding: 20px 28px; border-radius: 8px 8px 0 0;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="vertical-align: middle;">
                        <span style="color: #ffffff; font-size: 18px; font-weight: 700; letter-spacing: 0.3px;">MYTICAS</span>
                        <span style="color: #4FC3F7; font-size: 18px; font-weight: 300; letter-spacing: 0.3px;"> CONSULTING</span>
                    </td>
                    <td style="text-align: right; vertical-align: middle;">
                        <span style="color: rgba(255,255,255,0.6); font-size: 12px;">Internal Support</span>
                    </td>
                </tr>
            </table>
        </div>

        <div style="background: #2a5298; padding: 14px 28px;">
            <span style="color: #ffffff; font-size: 15px; font-weight: 600;">Support Request</span>
            <span style="color: rgba(255,255,255,0.7); font-size: 13px; margin-left: 8px;">— {category_icon} {category_label}</span>
        </div>

        <div style="background: #ffffff; padding: 24px 28px; border-left: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0;">
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 8px 0; color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; width: 130px; vertical-align: top;">Subject</td>
                    <td style="padding: 8px 0; color: #1e293b; font-size: 14px; font-weight: 600;">{subject}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; vertical-align: top;">From</td>
                    <td style="padding: 8px 0; color: #1e293b; font-size: 14px;">{requester_name} &lt;<a href="mailto:{requester_email}" style="color: #2a5298; text-decoration: none;">{requester_email}</a>&gt;</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; vertical-align: top;">Department</td>
                    <td style="padding: 8px 0; color: #1e293b; font-size: 14px;">{internal_department if internal_department else '<span style="color: #94a3b8; font-style: italic;">Not specified</span>'}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; vertical-align: top;">Priority</td>
                    <td style="padding: 8px 0; font-size: 14px;"><span style="color: {priority_color}; font-weight: 600;">●</span> <span style="color: #1e293b;">{priority_label}</span></td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; vertical-align: top;">Date</td>
                    <td style="padding: 8px 0; color: #64748b; font-size: 13px;">{datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')} UTC</td>
                </tr>
            </table>

            <div style="border-top: 1px solid #e2e8f0; padding-top: 16px;">
                <div style="color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 8px;">Description</div>
                <div style="background: #f8fafc; padding: 14px 18px; border-radius: 6px; border-left: 3px solid #2a5298; color: #334155; font-size: 14px; line-height: 1.65;">
                    {description_html}
                </div>
            </div>

            {attachment_section}
        </div>

        <div style="background: #f1f5f9; padding: 14px 28px; border-radius: 0 0 8px 8px; border: 1px solid #e2e8f0; border-top: none;">
            <p style="color: #64748b; font-size: 11px; margin: 0; text-align: center; line-height: 1.5;">
                Reply directly to this email to respond to the requester (<a href="mailto:{requester_email}" style="color: #2a5298; text-decoration: none;">{requester_email}</a>)
                <br>Myticas Consulting &middot; Powered by Scout Genius™
            </p>
        </div>
    </div>
    '''


def send_support_email(to_email, reply_to_email, subject, html_content, attachments=None, cc_emails=None):
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (
            Mail, Email, To, Content, Attachment,
            FileContent, FileName, FileType, Disposition, Cc
        )

        sg_api_key = os.environ.get('SENDGRID_API_KEY')
        if not sg_api_key:
            logger.error("SendGrid API key not configured")
            return False

        sg = SendGridAPIClient(sg_api_key)

        from_email = Email('kroots@myticas.com', 'Myticas Internal Support')
        to_email_obj = To(to_email)

        mail = Mail(
            from_email=from_email,
            to_emails=to_email_obj,
            subject=subject,
            html_content=Content('text/html', html_content)
        )

        mail.reply_to = Email(reply_to_email)

        if cc_emails:
            for cc_addr in cc_emails:
                if cc_addr and cc_addr != to_email:
                    mail.add_cc(Cc(cc_addr))

        if attachments:
            for att in attachments:
                encoded = base64.b64encode(att['data']).decode('utf-8')
                attachment = Attachment(
                    FileContent(encoded),
                    FileName(att['filename']),
                    FileType(att['content_type']),
                    Disposition('attachment')
                )
                mail.add_attachment(attachment)

        response = sg.client.mail.send.post(request_body=mail.get())

        if response.status_code == 202:
            cc_str = ', '.join(cc_emails) if cc_emails else 'none'
            logger.info(f"Support request email sent to {to_email} (CC: {cc_str}): {subject}")
            return True
        else:
            logger.error(f"Failed to send support email: status {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"Error sending support email: {e}", exc_info=True)
        return False
