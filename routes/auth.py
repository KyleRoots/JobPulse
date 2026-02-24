"""
Authentication routes for JobPulse.

Handles user login and logout functionality.
"""

from datetime import datetime
from urllib.parse import urlparse
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, current_user, login_required
from routes import _get_user_landing


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
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Please enter both username and password.', 'error')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
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
