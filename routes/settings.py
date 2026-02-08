"""
Settings routes for JobPulse.

Handles global settings for SFTP, email notifications, and automation configuration.
"""

from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from flask_login import login_required


settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/settings')
@login_required
def settings():
    """Global settings page for SFTP and email configuration"""
    from app import db
    from models import GlobalSettings
    
    try:
        # Get current settings
        settings_data = {}
        setting_keys = [
            'sftp_hostname', 'sftp_username', 'sftp_directory', 'sftp_port', 'sftp_enabled',
            'email_notifications_enabled', 'default_notification_email', 'automated_uploads_enabled'
        ]
        
        for key in setting_keys:
            setting = db.session.query(GlobalSettings).filter_by(setting_key=key).first()
            settings_data[key] = setting.setting_value if setting else ''
        
        return render_template('settings.html', settings=settings_data, active_page='settings')
        
    except Exception as e:
        current_app.logger.error(f"Error loading settings: {str(e)}")
        flash('Error loading settings', 'error')
        return redirect(url_for('bullhorn.bullhorn_dashboard'))


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
        
        db.session.commit()
        
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
