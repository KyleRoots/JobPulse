"""
Authentication routes for JobPulse.

Handles user login and logout functionality.
"""

from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, current_user, login_required


auth_bp = Blueprint('auth', __name__)


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
            
            # Redirect to originally requested page or index
            next_page = request.args.get('next')
            if next_page:
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
