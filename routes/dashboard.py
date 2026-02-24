"""
Dashboard routes for JobPulse.

Handles the main dashboard and root redirect.
"""

from flask import Blueprint, render_template, redirect, url_for, current_app
from flask_login import login_required, current_user
from routes import admin_required


dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def root():
    """Root endpoint - redirect to login or dashboard based on authentication"""
    from app import ensure_background_services
    
    if current_user.is_authenticated:
        ensure_background_services()
        if current_user.is_admin:
            return redirect(url_for('dashboard.dashboard_redirect'))
        else:
            return redirect(url_for('scout_inbound.scout_inbound_dashboard'))
    else:
        return redirect(url_for('auth.login'))


@dashboard_bp.route('/dashboard')
@login_required
@admin_required
def dashboard_redirect():
    """Main dashboard home page"""
    from app import db, ensure_background_services, get_automation_status
    from models import BullhornActivity, ScheduleConfig
    
    # Ensure scheduler is running for authenticated users
    ensure_background_services()
    
    # Get automation status
    try:
        automation_active = get_automation_status()
    except:
        automation_active = True  # Default to active if can't determine
    
    # Get recent activities (use BullhornActivity model that exists)
    try:
        recent_activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(5).all()
    except:
        recent_activities = []
    
    # Get latest schedules
    try:
        schedules = ScheduleConfig.query.filter_by(is_active=True).limit(3).all()
    except:
        schedules = []
    
    # Get environment status
    environment_status = None
    try:
        from models import EnvironmentStatus
        environment_status = EnvironmentStatus.query.filter_by(environment_name='production').first()
    except:
        environment_status = None
    
    # Dashboard metrics
    active_jobs = 0
    candidates_vetted = 0
    emails_sent = 0
    auto_fixed = 0
    
    try:
        # Active jobs: count from JobVettingRequirements (synced jobs from tearsheets)
        from models import JobVettingRequirements
        active_jobs = JobVettingRequirements.query.count()
    except Exception as e:
        current_app.logger.debug(f"Could not count active jobs: {e}")
    
    try:
        # Candidates vetted: count from CandidateVettingLog where status='completed'
        from models import CandidateVettingLog
        candidates_vetted = CandidateVettingLog.query.filter_by(status='completed').count()
    except Exception as e:
        current_app.logger.debug(f"Could not count vetted candidates: {e}")
    
    try:
        # Emails sent: count from EmailDeliveryLog where delivery_status='sent'
        from models import EmailDeliveryLog
        emails_sent = EmailDeliveryLog.query.filter_by(delivery_status='sent').count()
    except Exception as e:
        current_app.logger.debug(f"Could not count emails sent: {e}")
    
    try:
        # Auto-fixed issues: count BullhornActivity entries with type 'job_modified' (auto-corrected data)
        auto_fixed = BullhornActivity.query.filter_by(activity_type='job_modified').count()
    except Exception as e:
        current_app.logger.debug(f"Could not count auto-fixed issues: {e}")
    
    return render_template('dashboard.html', 
                         active_page='dashboard',
                         recent_activities=recent_activities,
                         automation_active=automation_active,
                         schedules=schedules,
                         environment_status=environment_status,
                         active_jobs=active_jobs,
                         candidates_vetted=candidates_vetted,
                         emails_sent=emails_sent,
                         auto_fixed=auto_fixed)
