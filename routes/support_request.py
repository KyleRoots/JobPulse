import os
import logging
import base64
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, make_response, session
from extensions import csrf
from werkzeug.utils import secure_filename
from functools import wraps
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
    'general_inquiry': 'General Inquiry',
    'other': 'Other',
}

CATEGORY_ICONS = {
    'ats_issue': '🔧',
    'candidate_parsing': '⚠️',
    'job_posting': '📋',
    'account_access': '🔑',
    'email_notifications': '📧',
    'data_correction': '📝',
    'feature_request': '💡',
    'general_inquiry': '❓',
    'other': '📌',
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

SUPPORT_ROUTING = {
    'default': 'kroots@myticas.com',
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xlsx', 'csv', 'txt'}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_FILES = 5

_rate_limit_store = {}
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 5


def get_route_email(category):
    return SUPPORT_ROUTING.get(category, SUPPORT_ROUTING['default'])


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


@support_request_bp.route('/support')
@csrf.exempt
def support_form():
    response = make_response(render_template('support_request.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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

        route_email = get_route_email(category)
        category_label = CATEGORY_LABELS.get(category, category)
        category_icon = CATEGORY_ICONS.get(category, '📌')
        priority_label = PRIORITY_LABELS.get(priority, priority)
        priority_color = PRIORITY_COLORS.get(priority, '#ffc107')

        html_content = build_support_email_html(
            requester_name=requester_name,
            requester_email=requester_email,
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


def build_support_email_html(requester_name, requester_email, category_label, category_icon,
                              priority_label, priority_color, subject, description, attachments):
    attachment_section = ''
    if attachments:
        file_list = ''.join(
            f'<li style="padding: 4px 0; color: #334155;">'
            f'📎 {att["filename"]} ({len(att["data"]) / 1024:.1f} KB)</li>'
            for att in attachments
        )
        attachment_section = f'''
        <div style="margin-top: 20px;">
            <h3 style="color: #1e3c72; font-size: 16px; margin-bottom: 8px;">
                📎 Attachments ({len(attachments)} file{"s" if len(attachments) > 1 else ""})
            </h3>
            <ul style="margin: 0; padding-left: 20px;">{file_list}</ul>
            <p style="color: #94a3b8; font-size: 12px; margin-top: 8px;">Files are attached to this email.</p>
        </div>
        '''

    description_html = description.replace('\n', '<br>')

    return f'''
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 650px; margin: 0 auto; background: #f8fafc;">
        <div style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); padding: 24px 30px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 22px; font-weight: 600;">
                🎫 Internal Support Request
            </h1>
            <p style="color: rgba(255,255,255,0.8); margin: 6px 0 0 0; font-size: 14px;">
                Submitted via Myticas Internal Support Form
            </p>
        </div>

        <div style="background: white; padding: 30px; border: 1px solid #e2e8f0; border-top: none;">
            <div style="display: flex; margin-bottom: 20px;">
                <div style="background: {priority_color}15; border: 1px solid {priority_color}40; border-radius: 8px; padding: 3px 12px; display: inline-block;">
                    <span style="color: {priority_color}; font-weight: 600; font-size: 13px;">● {priority_label} Priority</span>
                </div>
            </div>

            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 10px 12px; color: #64748b; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #f1f5f9; width: 140px;">From</td>
                    <td style="padding: 10px 12px; color: #1e293b; font-size: 14px; border-bottom: 1px solid #f1f5f9;">
                        {requester_name} &lt;<a href="mailto:{requester_email}" style="color: #2563eb; text-decoration: none;">{requester_email}</a>&gt;
                    </td>
                </tr>
                <tr>
                    <td style="padding: 10px 12px; color: #64748b; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #f1f5f9;">Category</td>
                    <td style="padding: 10px 12px; color: #1e293b; font-size: 14px; border-bottom: 1px solid #f1f5f9;">{category_icon} {category_label}</td>
                </tr>
                <tr>
                    <td style="padding: 10px 12px; color: #64748b; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #f1f5f9;">Subject</td>
                    <td style="padding: 10px 12px; color: #1e293b; font-size: 14px; font-weight: 600; border-bottom: 1px solid #f1f5f9;">{subject}</td>
                </tr>
                <tr>
                    <td style="padding: 10px 12px; color: #64748b; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px;">Submitted</td>
                    <td style="padding: 10px 12px; color: #1e293b; font-size: 14px;">{datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')} UTC</td>
                </tr>
            </table>

            <div style="margin-top: 20px;">
                <h3 style="color: #1e3c72; font-size: 16px; margin-bottom: 10px;">📝 Description</h3>
                <div style="background: #f8fafc; padding: 16px 20px; border-radius: 8px; border: 1px solid #e2e8f0; color: #334155; font-size: 14px; line-height: 1.6;">
                    {description_html}
                </div>
            </div>

            {attachment_section}
        </div>

        <div style="background: #f1f5f9; padding: 16px 30px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
            <p style="color: #64748b; font-size: 12px; margin: 0; text-align: center;">
                Reply directly to this email to respond to the requester ({requester_email}).
                <br>Powered by Scout Genius™ Internal Support
            </p>
        </div>
    </div>
    '''


def send_support_email(to_email, reply_to_email, subject, html_content, attachments=None):
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (
            Mail, Email, To, Content, Attachment,
            FileContent, FileName, FileType, Disposition
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
            logger.info(f"Support request email sent to {to_email}: {subject}")
            return True
        else:
            logger.error(f"Failed to send support email: status {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"Error sending support email: {e}", exc_info=True)
        return False
