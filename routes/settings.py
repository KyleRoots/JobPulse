"""
Settings routes for JobPulse.

Handles global settings for SFTP, email notifications, automation configuration,
and user management (create/edit users, toggle module subscriptions).
"""

import json
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app, session
from flask_login import login_required, login_user, current_user
from routes import register_admin_guard


settings_bp = Blueprint('settings', __name__)
register_admin_guard(settings_bp)


@settings_bp.route('/settings')
@login_required
def settings():
    """Global settings page for SFTP and email configuration"""
    from app import db
    from models import GlobalSettings
    
    try:
        from models import User, AVAILABLE_MODULES
        
        settings_data = {}
        setting_keys = [
            'sftp_hostname', 'sftp_username', 'sftp_directory', 'sftp_port', 'sftp_enabled',
            'email_notifications_enabled', 'default_notification_email', 'automated_uploads_enabled'
        ]
        
        for key in setting_keys:
            setting = db.session.query(GlobalSettings).filter_by(setting_key=key).first()
            settings_data[key] = setting.setting_value if setting else ''
        
        users = current_user.get_visible_users()

        from models import SupportContact
        support_contacts = SupportContact.query.filter_by(brand='Myticas').order_by(
            SupportContact.first_name, SupportContact.last_name
        ).all()

        return render_template('settings.html',
                             settings=settings_data,
                             users=users,
                             available_modules=AVAILABLE_MODULES,
                             support_contacts=support_contacts,
                             active_page='settings')
        
    except Exception as e:
        current_app.logger.error(f"Error loading settings: {str(e)}")
        flash('Error loading settings', 'error')
        return redirect(url_for('ats_integration.ats_integration_dashboard'))


@settings_bp.route('/settings', methods=['POST'])
def update_settings():
    """Update global settings"""
    from app import db, scheduler, automated_upload
    from models import GlobalSettings
    
    try:
        # Update SFTP settings
        sftp_settings = {
            'sftp_enabled': 'true' if request.form.get('sftp_enabled') == 'on' else 'false',
            'sftp_hostname': request.form.get('sftp_hostname', ''),
            'sftp_username': request.form.get('sftp_username', ''),
            'sftp_password': request.form.get('sftp_password', ''),
            'sftp_directory': request.form.get('sftp_directory', '/'),
            'sftp_port': request.form.get('sftp_port', '2222')
        }
        
        # Update email settings
        email_settings = {
            'email_notifications_enabled': 'true' if request.form.get('email_notifications_enabled') == 'on' else 'false',
            'default_notification_email': request.form.get('default_notification_email', '')
        }
        
        # Update automation settings
        automation_settings = {
            'automated_uploads_enabled': 'true' if request.form.get('automated_uploads_enabled') == 'on' else 'false'
        }
        
        # Combine all settings
        all_settings = {**sftp_settings, **email_settings, **automation_settings}
        
        # Check if automation setting changed to manage scheduler job
        old_automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        old_automation_enabled = old_automation_setting.setting_value == 'true' if old_automation_setting else False
        new_automation_enabled = automation_settings['automated_uploads_enabled'] == 'true'
        
        # Save to database
        for key, value in all_settings.items():
            # Skip password if empty (preserve existing password)
            if key == 'sftp_password' and not value:
                continue
                
            setting = db.session.query(GlobalSettings).filter_by(setting_key=key).first()
            
            if setting:
                setting.setting_value = str(value)
                setting.updated_at = datetime.utcnow()
            else:
                setting = GlobalSettings(
                    setting_key=key,
                    setting_value=str(value)
                )
                db.session.add(setting)
        
        # Retry-on-lock: handles SQLite concurrency and transient DB errors
        from sqlalchemy.exc import OperationalError
        import time
        for attempt in range(3):
            try:
                db.session.commit()
                break
            except OperationalError as oe:
                if 'database is locked' in str(oe) and attempt < 2:
                    db.session.rollback()
                    time.sleep(0.5 * (attempt + 1))
                    current_app.logger.warning(f"⚠️ DB lock on settings save, retry {attempt + 1}/3")
                else:
                    raise
        
        # Manage automated upload scheduler job if setting changed
        if old_automation_enabled != new_automation_enabled:
            try:
                if new_automation_enabled:
                    # Add automated upload job
                    if scheduler.get_job('automated_upload') is None:
                        scheduler.add_job(
                            func=automated_upload,
                            trigger='interval',
                            minutes=30,
                            id='automated_upload',
                            name='Automated Upload (Every 30 Minutes)',
                            replace_existing=True
                        )
                        current_app.logger.info("📤 Automated uploads enabled - 30-minute job added to scheduler")
                        flash('Automated uploads enabled! XML files will be uploaded every 30 minutes.', 'success')
                    else:
                        current_app.logger.info("📤 Automated upload job already exists")
                        flash('Automated uploads enabled!', 'success')
                else:
                    # Remove automated upload job
                    try:
                        scheduler.remove_job('automated_upload')
                        current_app.logger.info("📋 Automated uploads disabled - job removed from scheduler")
                        flash('Automated uploads disabled. Manual download workflow activated.', 'info')
                    except:
                        current_app.logger.info("📋 Automated upload job was not scheduled")
                        flash('Automated uploads disabled.', 'info')
            except Exception as scheduler_error:
                current_app.logger.error(f"Failed to update automation scheduler: {str(scheduler_error)}")
                flash('Settings saved but scheduler update failed. Restart application to apply automation changes.', 'warning')
        else:
            flash('Settings updated successfully!', 'success')
            
        return redirect(url_for('settings.settings'))
        
    except Exception as e:
        current_app.logger.error(f"Error updating settings: {str(e)}")
        db.session.rollback()
        flash(f'Error updating settings: {str(e)}', 'error')
        return redirect(url_for('settings.settings'))


@settings_bp.route('/test-sftp-connection', methods=['POST'])
def test_sftp_connection():
    """Test SFTP connection with form data"""
    from app import db
    from models import GlobalSettings
    from ftp_service import FTPService
    
    try:
        # Get form data from request
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No connection data provided'
            })
        
        hostname = data.get('sftp_hostname', '').strip()
        username = data.get('sftp_username', '').strip()
        password = data.get('sftp_password', '').strip()
        directory = data.get('sftp_directory', '/').strip()
        port = data.get('sftp_port', '2222')
        
        # If password is empty, try to use the saved password from database
        # (Password field is intentionally empty by default for security)
        if not password:
            saved_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
            if saved_password and saved_password.setting_value:
                password = saved_password.setting_value
                current_app.logger.info("Using saved password from database for SFTP test")
        
        if not all([hostname, username, password]):
            return jsonify({
                'success': False,
                'error': 'Please fill in hostname, username, and password fields (or ensure password is saved).'
            })
        
        # Convert port to integer
        try:
            port = int(port) if port else 2222
        except ValueError:
            port = 2222
        
        # Test connection
        ftp_service = FTPService(
            hostname=hostname,
            username=username,
            password=password,
            target_directory=directory,
            port=port,
            use_sftp=True
        )
        
        current_app.logger.info(f"Testing SFTP connection to {hostname}:{port} with user {username}")
        result = ftp_service.test_connection()
        
        # Handle dict response (SFTP) or bool response (FTP)
        if isinstance(result, dict):
            if result.get('success'):
                return jsonify({
                    'success': True,
                    'message': f'Successfully connected to {hostname} on port {port}!'
                })
            else:
                error_msg = result.get('error', 'Unknown error')
                current_app.logger.error(f"SFTP test failed: {error_msg}")
                return jsonify({
                    'success': False,
                    'error': error_msg
                })
        else:
            # Legacy bool response (FTP)
            if result:
                return jsonify({
                    'success': True,
                    'message': f'Successfully connected to {hostname} on port {port}!'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Failed to connect. Please check your credentials and try again.'
                })
        
    except Exception as e:
        current_app.logger.error(f"Error testing SFTP connection: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Connection test failed: {str(e)}'
        })


@settings_bp.route('/settings/users/create', methods=['POST'])
@login_required
def create_user():
    """Create a new user account with module subscriptions."""
    from app import db
    from models import User, AVAILABLE_MODULES
    
    try:
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        display_name = request.form.get('display_name', '').strip() or None
        bullhorn_user_id = request.form.get('bullhorn_user_id', '').strip()
        company = request.form.get('company', '').strip() or 'Myticas Consulting'
        is_admin = request.form.get('is_admin') == 'on'
        is_company_admin = request.form.get('is_company_admin') == 'on'
        if is_admin:
            is_company_admin = False

        selected_modules = request.form.getlist('modules')

        if not username or not email:
            flash('Username and email are required.', 'error')
            return redirect(url_for('settings.settings') + '#user-management')

        if User.query.filter_by(username=username).first():
            flash(f'Username "{username}" already exists.', 'error')
            return redirect(url_for('settings.settings') + '#user-management')

        if User.query.filter_by(email=email).first():
            flash(f'Email "{email}" already exists.', 'error')
            return redirect(url_for('settings.settings') + '#user-management')

        user = User(
            username=username,
            email=email,
            is_admin=is_admin,
            is_company_admin=is_company_admin,
            role='super_admin' if is_admin else ('company_admin' if is_company_admin else 'user'),
            company=company,
            display_name=display_name,
            bullhorn_user_id=int(bullhorn_user_id) if bullhorn_user_id else None,
        )
        if password:
            user.set_password(password)
        else:
            import secrets as _secrets
            user.set_password(_secrets.token_hex(32))
        user.set_modules(selected_modules)

        db.session.add(user)
        db.session.commit()

        flash(f'User "{username}" created. Send them a welcome email to let them set their password.', 'success')
        return redirect(url_for('settings.settings') + '#user-management')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating user: {str(e)}")
        flash(f'Error creating user: {str(e)}', 'error')
        return redirect(url_for('settings.settings') + '#user-management')


@settings_bp.route('/settings/users/<int:user_id>/update', methods=['POST'])
@login_required
def update_user(user_id):
    """Update an existing user's modules, display name, and Bullhorn ID."""
    from app import db
    from models import User, AVAILABLE_MODULES
    
    try:
        user = User.query.get_or_404(user_id)
        
        display_name = request.form.get('display_name', '').strip() or None
        bullhorn_user_id = request.form.get('bullhorn_user_id', '').strip()
        is_admin = request.form.get('is_admin') == 'on'
        is_company_admin = request.form.get('is_company_admin') == 'on'
        if is_admin:
            is_company_admin = False
        selected_modules = request.form.getlist('modules')
        new_password = request.form.get('password', '').strip()

        company = request.form.get('company', '').strip() or user.company
        user.display_name = display_name
        user.bullhorn_user_id = int(bullhorn_user_id) if bullhorn_user_id else None
        user.is_admin = is_admin
        user.is_company_admin = is_company_admin
        user.role = 'super_admin' if is_admin else ('company_admin' if is_company_admin else 'user')
        if current_user.is_admin:
            user.company = company
        user.set_modules(selected_modules)
        
        if new_password:
            user.set_password(new_password)
        
        db.session.commit()
        
        flash(f'User "{user.username}" updated successfully.', 'success')
        return redirect(url_for('settings.settings') + '#user-management')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating user: {str(e)}")
        flash(f'Error updating user: {str(e)}', 'error')
        return redirect(url_for('settings.settings') + '#user-management')


@settings_bp.route('/settings/users/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    """Delete a user account."""
    from app import db
    from models import User
    from flask_login import current_user
    
    try:
        user = User.query.get_or_404(user_id)
        
        if user.id == current_user.id:
            flash('You cannot delete your own account.', 'error')
            return redirect(url_for('settings.settings') + '#user-management')
        
        username = user.username
        db.session.delete(user)
        db.session.commit()
        
        flash(f'User "{username}" deleted.', 'success')
        return redirect(url_for('settings.settings') + '#user-management')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting user: {str(e)}")
        flash(f'Error deleting user: {str(e)}', 'error')
        return redirect(url_for('settings.settings') + '#user-management')


@settings_bp.route('/settings/users/<int:user_id>/impersonate', methods=['POST'])
@login_required
def impersonate_user(user_id):
    """Start impersonating another user. Admin-only."""
    from app import db
    from models import User

    if not current_user.can_view_all_users:
        flash('Only administrators can use View As.', 'error')
        return redirect(url_for('settings.settings'))

    if session.get('impersonating_admin_id'):
        flash('You are already viewing as another user. Return to your account first.', 'warning')
        return redirect(url_for('settings.settings'))

    target_user = User.query.get_or_404(user_id)

    if target_user.id == current_user.id:
        flash('You cannot view as yourself.', 'warning')
        return redirect(url_for('settings.settings') + '#user-management')

    if current_user.is_company_admin:
        if target_user.is_admin or target_user.is_company_admin:
            flash('Company admins can only view as standard users.', 'warning')
            return redirect(url_for('settings.settings') + '#user-management')
        if target_user.company != current_user.company:
            flash('You can only view users within your own company.', 'warning')
            return redirect(url_for('settings.settings') + '#user-management')

    session['impersonating_admin_id'] = current_user.id
    session['impersonating_admin_username'] = current_user.username
    login_user(target_user, remember=False)

    current_app.logger.info(f"{current_user.effective_role} started viewing as: {target_user.username} (id={target_user.id})")
    flash(f'Now viewing as {target_user.display_name or target_user.username}.', 'info')

    from routes import _get_user_landing
    return redirect(_get_user_landing())


@settings_bp.route('/settings/users/<int:user_id>/send-welcome', methods=['POST'])
@login_required
def send_welcome_email_route(user_id):
    """Send a welcome / set-password email to the specified user. Admin-only."""
    from models import User
    from routes.auth import generate_reset_token, send_welcome_email
    from flask import url_for

    if not current_user.is_admin:
        flash('Only administrators can send welcome emails.', 'error')
        return redirect(url_for('settings.settings') + '#user-management')

    user = User.query.get_or_404(user_id)
    token = generate_reset_token(user, expiry_hours=48)
    set_password_url = url_for('auth.reset_password', token=token, _external=True)
    success = send_welcome_email(user, set_password_url)

    if success:
        flash(f'Welcome email sent to {user.email}.', 'success')
    else:
        flash(f'Failed to send welcome email to {user.email}. Check SendGrid configuration.', 'error')

    return redirect(url_for('settings.settings') + '#user-management')


@settings_bp.route('/settings/users/<int:user_id>/send-reset', methods=['POST'])
@login_required
def send_reset_email_route(user_id):
    """Send a password reset email to the specified user. Admin-only."""
    from models import User
    from routes.auth import generate_reset_token, send_password_reset_email
    from flask import url_for

    if not current_user.is_admin:
        flash('Only administrators can send password reset emails.', 'error')
        return redirect(url_for('settings.settings') + '#user-management')

    user = User.query.get_or_404(user_id)
    token = generate_reset_token(user, expiry_hours=1)
    reset_url = url_for('auth.reset_password', token=token, _external=True)
    success = send_password_reset_email(user, reset_url)

    if success:
        flash(f'Password reset email sent to {user.email}.', 'success')
    else:
        flash(f'Failed to send reset email to {user.email}. Check SendGrid configuration.', 'error')

    return redirect(url_for('settings.settings') + '#user-management')


@settings_bp.route('/settings/users/bulk-send', methods=['POST'])
@login_required
def bulk_send_emails():
    """Send welcome or reset emails to a selection of users. Admin-only."""
    from models import User
    from routes.auth import generate_reset_token, send_welcome_email, send_password_reset_email
    from flask import request as req

    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    data = req.get_json() or {}
    user_ids = data.get('user_ids', [])
    action = data.get('action', 'welcome')

    if not user_ids:
        return jsonify({'error': 'No users selected'}), 400
    if action not in ('welcome', 'reset'):
        return jsonify({'error': 'Invalid action'}), 400

    success_count = 0
    failed_count = 0

    for uid in user_ids:
        try:
            user = User.query.get(int(uid))
            if not user:
                failed_count += 1
                continue
            if action == 'welcome':
                token = generate_reset_token(user, expiry_hours=48)
                send_url = url_for('auth.reset_password', token=token, _external=True)
                ok = send_welcome_email(user, send_url)
            else:
                token = generate_reset_token(user, expiry_hours=1)
                send_url = url_for('auth.reset_password', token=token, _external=True)
                ok = send_password_reset_email(user, send_url)
            if ok:
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            current_app.logger.error(f"Bulk send error for user {uid}: {e}")
            failed_count += 1

    return jsonify({'success': success_count, 'failed': failed_count})
