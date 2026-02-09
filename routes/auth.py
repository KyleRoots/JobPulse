"""
Authentication routes for JobPulse.

Handles user login and logout functionality.
"""

from datetime import datetime
from urllib.parse import urlparse
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, current_user, login_required


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
        return redirect(url_for('dashboard.dashboard_redirect'))
    
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
            
            # Redirect to originally requested page or dashboard (safe URLs only)
            next_page = request.args.get('next')
            if next_page and _is_safe_redirect_url(next_page):
                return redirect(next_page)
            # Force scroll to top by adding fragment
            return redirect(url_for('dashboard.dashboard_redirect') + '#top')
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('auth.login'))
