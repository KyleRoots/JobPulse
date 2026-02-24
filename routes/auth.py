"""
Authentication routes for Scout Genius.

Handles user login, logout, and password reset functionality.
"""

import os
import secrets
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, current_user, login_required
from routes import _get_user_landing

logger = logging.getLogger(__name__)


auth_bp = Blueprint('auth', __name__)


def _is_safe_redirect_url(target):
    """
    Validate that a redirect target is safe (relative, same-origin).
    
    Rejects:
    - External URLs (https://evil.com)
    - Protocol-relative URLs (//evil.com)
    - URLs with a netloc different from the request host
    - Empty or whitespace-only strings
    """
    if not target or not target.strip():
        return False
    parsed = urlparse(target)
    # Reject if scheme is present (http://, https://, javascript:, etc.)
    if parsed.scheme:
        return False
    # Reject protocol-relative URLs (//evil.com)
    if target.lstrip().startswith('//'):
        return False
    # Reject if netloc is present
    if parsed.netloc:
        return False
    return True


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login page"""
    # Import here to avoid circular imports
    from app import db, User, ensure_background_services
    
    if current_user.is_authenticated:
        return redirect(_get_user_landing())
    
    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password')
        
        if not identifier or not password:
            flash('Please enter your username or email and password.', 'error')
            return render_template('login.html')
        
        from extensions import db as _db
        user = User.query.filter(
            (User.username == identifier) |
            (_db.func.lower(User.email) == identifier.lower())
        ).first()
        if user and user.check_password(password):
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            login_user(user, remember=True)  # Remember user for extended session
            session.permanent = True  # Enable 30-day session persistence
            # Removed welcome message for cleaner login experience
            
            # Start scheduler on successful login
            ensure_background_services()
            
            next_page = request.args.get('next')
            if next_page and _is_safe_redirect_url(next_page):
                return redirect(next_page)
            return redirect(_get_user_landing())
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html')


@auth_bp.route('/stop-impersonation')
@login_required
def stop_impersonation():
    """Stop impersonating a user and return to admin account."""
    from app import db
    from models import User

    admin_id = session.pop('impersonating_admin_id', None)
    session.pop('impersonating_admin_username', None)

    if not admin_id:
        flash('No active impersonation session.', 'warning')
        return redirect(_get_user_landing())

    admin_user = User.query.get(admin_id)
    if not admin_user or not admin_user.is_admin:
        flash('Could not restore admin session.', 'error')
        logout_user()
        return redirect(url_for('auth.login'))

    login_user(admin_user, remember=True)
    session.permanent = True
    flash('Returned to your admin account.', 'success')
    return redirect(url_for('settings.settings') + '#user-management')


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout"""
    session.pop('impersonating_admin_id', None)
    session.pop('impersonating_admin_username', None)
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Send a password reset link to the user's email."""
    from extensions import db
    from models import User, PasswordResetToken

    if current_user.is_authenticated:
        return redirect(_get_user_landing())

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            flash('Please enter your email address.', 'error')
            return render_template('forgot_password.html')

        user = User.query.filter(
            db.func.lower(User.email) == email
        ).first()

        if user:
            token = generate_reset_token(user, expiry_hours=1)
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            send_password_reset_email(user, reset_url)
            logger.info(f"Password reset requested for {user.email}")

        flash('If that email is registered, a reset link has been sent. Check your inbox.', 'success')
        return render_template('forgot_password.html')

    return render_template('forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Allow the user to set a new password using a valid reset token."""
    from extensions import db
    from models import PasswordResetToken

    if current_user.is_authenticated:
        return redirect(_get_user_landing())

    reset_token = PasswordResetToken.query.filter_by(token=token).first()

    if not reset_token or not reset_token.is_valid:
        flash('This reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()

        if not password or len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset_password.html', token=token)

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)

        reset_token.user.set_password(password)
        reset_token.used = True
        db.session.commit()
        logger.info(f"Password reset completed for {reset_token.user.email}")

        flash('Your password has been updated. You can now sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token)


def generate_reset_token(user, expiry_hours=1):
    """Generate a single-use password reset/welcome token for a user."""
    from extensions import db
    from models import PasswordResetToken
    PasswordResetToken.query.filter_by(user_id=user.id, used=False).delete()
    token = secrets.token_hex(32)
    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=expiry_hours)
    )
    db.session.add(reset_token)
    db.session.commit()
    return token


def send_welcome_email(user, set_password_url):
    """Send a Scout Genius branded welcome / set-your-password email via SendGrid."""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg_api_key = os.environ.get('SENDGRID_API_KEY')
        if not sg_api_key:
            logger.error("SendGrid API key not configured — cannot send welcome email")
            return False
        display_name = user.display_name or user.username
        html_content = _build_email_html(
            display_name=display_name,
            heading='Welcome to Scout Genius',
            subheading='Account Activation',
            body_line1=f"Your Scout Genius account has been created. Click the button below to set your password and get started.",
            body_line2="This link expires in <strong style=\"color: #4a9678;\">48 hours</strong>. If it expires, use the Forgot Password link on the sign-in page.",
            button_text='Set My Password',
            button_url=set_password_url,
            footer_note="If you weren't expecting this, please contact your administrator.",
            year=datetime.utcnow().year
        )
        sg = SendGridAPIClient(sg_api_key)
        mail = Mail(
            from_email=Email('kroots@myticas.com', 'Scout Genius'),
            to_emails=To(user.email),
            subject='Welcome to Scout Genius — Set Your Password',
            html_content=Content('text/html', html_content)
        )
        resp = sg.client.mail.send.post(request_body=mail.get())
        if resp.status_code == 202:
            logger.info(f"Welcome email sent to {user.email}")
            return True
        logger.error(f"Welcome email failed: status {resp.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error sending welcome email: {e}", exc_info=True)
        return False


def send_password_reset_email(user, reset_url):
    """Send a Scout Genius branded password reset email via SendGrid."""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg_api_key = os.environ.get('SENDGRID_API_KEY')
        if not sg_api_key:
            logger.error("SendGrid API key not configured — cannot send reset email")
            return False
        display_name = user.display_name or user.username
        html_content = _build_email_html(
            display_name=display_name,
            heading='Password Reset',
            subheading='Password Reset',
            body_line1="We received a request to reset the password for your Scout Genius account. Click the button below to set a new password.",
            body_line2="This link expires in <strong style=\"color: #4a9678;\">1 hour</strong>.",
            button_text='Reset My Password',
            button_url=reset_url,
            footer_note="If you didn't request this, you can safely ignore this email — your password will remain unchanged.",
            year=datetime.utcnow().year
        )
        sg = SendGridAPIClient(sg_api_key)
        mail = Mail(
            from_email=Email('kroots@myticas.com', 'Scout Genius'),
            to_emails=To(user.email),
            subject='Reset Your Scout Genius Password',
            html_content=Content('text/html', html_content)
        )
        resp = sg.client.mail.send.post(request_body=mail.get())
        if resp.status_code == 202:
            logger.info(f"Reset email sent to {user.email}")
            return True
        logger.error(f"Reset email failed: status {resp.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error sending reset email: {e}", exc_info=True)
        return False


def _build_email_html(display_name, heading, subheading, body_line1, body_line2,
                      button_text, button_url, footer_note, year):
    """Build the shared Scout Genius email HTML body."""
    return f'''
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 580px; margin: 0 auto; background: #0a1f16; border-radius: 12px; overflow: hidden;">
        <div style="background: linear-gradient(135deg, #1e3a2a 0%, #0d2817 100%); padding: 32px 36px; border-bottom: 1px solid rgba(74,150,120,0.3);">
            <div style="text-align: center;">
                <div style="color: #ffffff; font-size: 22px; font-weight: 700; letter-spacing: -0.02em;">
                    <span style="color: #4a9678;">&#9679;</span> Scout Genius<span style="color: #4a9678;">&#8482;</span>
                </div>
                <div style="color: rgba(255,255,255,0.5); font-size: 12px; margin-top: 4px; letter-spacing: 0.5px; text-transform: uppercase;">{subheading}</div>
            </div>
        </div>
        <div style="padding: 36px; background: #0f2318;">
            <p style="color: rgba(255,255,255,0.85); font-size: 15px; margin: 0 0 12px 0;">Hi {display_name},</p>
            <p style="color: rgba(255,255,255,0.7); font-size: 14px; line-height: 1.6; margin: 0 0 8px 0;">{body_line1}</p>
            <p style="color: rgba(255,255,255,0.7); font-size: 14px; line-height: 1.6; margin: 0 0 28px 0;">{body_line2}</p>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 32px auto;">
                <tr>
                    <td style="border-radius: 10px; background-color: #4a9678;">
                        <a href="{button_url}" style="display: inline-block; background-color: #4a9678; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 10px; font-weight: 700; font-size: 15px; letter-spacing: 0.4px; font-family: Arial, sans-serif; mso-padding-alt: 14px 40px;">
                            {button_text}
                        </a>
                    </td>
                </tr>
            </table>
            <p style="color: rgba(255,255,255,0.45); font-size: 13px; line-height: 1.6; margin: 28px 0 0 0;">{footer_note}</p>
            <div style="margin-top: 28px; padding-top: 20px; border-top: 1px solid rgba(74,150,120,0.2);">
                <p style="color: rgba(255,255,255,0.3); font-size: 12px; margin: 0; word-break: break-all;">
                    Or copy this link into your browser:<br>
                    <span style="color: rgba(74,150,120,0.7);">{button_url}</span>
                </p>
            </div>
        </div>
        <div style="background: #0a1a10; padding: 16px 36px; text-align: center; border-top: 1px solid rgba(74,150,120,0.15);">
            <p style="color: rgba(255,255,255,0.3); font-size: 11px; margin: 0;">
                &copy; {year} Scout Genius. All rights reserved.
            </p>
        </div>
    </div>
    '''


@auth_bp.route('/dev/email-preview/reset')
def dev_email_preview():
    """Dev-only: preview the password reset email in the browser."""
    import os as _os
    env = (_os.environ.get('APP_ENV') or _os.environ.get('ENVIRONMENT') or 'production').lower()
    if env == 'production':
        from flask import abort
        abort(404)

    preview_url = '#your-reset-link-would-appear-here'
    html_body = f'''
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 580px; margin: 40px auto; background: #0a1f16; border-radius: 12px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.4);">
        <div style="background: linear-gradient(135deg, #1e3a2a 0%, #0d2817 100%); padding: 32px 36px; border-bottom: 1px solid rgba(74,150,120,0.3);">
            <div style="text-align: center;">
                <div style="color: #ffffff; font-size: 22px; font-weight: 700; letter-spacing: -0.02em;">
                    <span style="color: #4a9678;">&#9679;</span> Scout Genius<span style="color: #4a9678;">&#8482;</span>
                </div>
                <div style="color: rgba(255,255,255,0.5); font-size: 12px; margin-top: 4px; letter-spacing: 0.5px; text-transform: uppercase;">Password Reset</div>
            </div>
        </div>
        <div style="padding: 36px; background: #0f2318;">
            <p style="color: rgba(255,255,255,0.85); font-size: 15px; margin: 0 0 12px 0;">Hi Kyle Roots,</p>
            <p style="color: rgba(255,255,255,0.7); font-size: 14px; line-height: 1.6; margin: 0 0 28px 0;">
                We received a request to reset the password for your Scout Genius account. Click the button below to set a new password. This link expires in <strong style="color: #4a9678;">1 hour</strong>.
            </p>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 32px auto;">
                <tr>
                    <td style="border-radius: 10px; background-color: #4a9678;">
                        <a href="{preview_url}" style="display: inline-block; background-color: #4a9678; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 10px; font-weight: 700; font-size: 15px; letter-spacing: 0.4px; font-family: Arial, sans-serif;">
                            Reset My Password
                        </a>
                    </td>
                </tr>
            </table>
            <p style="color: rgba(255,255,255,0.45); font-size: 13px; line-height: 1.6; margin: 28px 0 0 0;">
                If you didn't request this, you can safely ignore this email &mdash; your password will remain unchanged.
            </p>
            <div style="margin-top: 28px; padding-top: 20px; border-top: 1px solid rgba(74,150,120,0.2);">
                <p style="color: rgba(255,255,255,0.3); font-size: 12px; margin: 0; word-break: break-all;">
                    Or copy this link into your browser:<br>
                    <span style="color: rgba(74,150,120,0.7);">{preview_url}</span>
                </p>
            </div>
        </div>
        <div style="background: #0a1a10; padding: 16px 36px; text-align: center; border-top: 1px solid rgba(74,150,120,0.15);">
            <p style="color: rgba(255,255,255,0.3); font-size: 11px; margin: 0;">
                &copy; 2026 Scout Genius. All rights reserved.
            </p>
        </div>
    </div>
    '''
    from flask import Response
    page = f'<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Email Preview</title><style>body{{background:#1a1a2e;margin:0;padding:20px;}}</style></head><body>{html_body}</body></html>'
    return Response(page, mimetype='text/html')


