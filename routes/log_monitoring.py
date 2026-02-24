import json
import logging
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from routes import register_admin_guard
from extensions import db

logger = logging.getLogger(__name__)
log_monitoring_bp = Blueprint('log_monitoring', __name__)
register_admin_guard(log_monitoring_bp)


@log_monitoring_bp.route('/log-monitoring')
@login_required
def log_monitoring_page():
    """Log monitoring dashboard page."""
    return render_template('log_monitoring.html', active_page='log_monitoring')

@log_monitoring_bp.route('/api/log-monitoring/status')
@login_required
def api_log_monitoring_status():
    """Get current log monitoring status."""
    try:
        from log_monitoring_service import get_log_monitor
        monitor = get_log_monitor()
        return jsonify({
            "success": True,
            "status": monitor.get_status()
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@log_monitoring_bp.route('/api/log-monitoring/history')
@login_required
def api_log_monitoring_history():
    """Get recent log monitoring history."""
    try:
        from log_monitoring_service import get_log_monitor
        monitor = get_log_monitor()
        limit = request.args.get('limit', 10, type=int)
        return jsonify({
            "success": True,
            "history": monitor.get_history(limit)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@log_monitoring_bp.route('/api/log-monitoring/run', methods=['POST'])
@login_required
def api_log_monitoring_run():
    """Manually trigger a log monitoring cycle."""
    try:
        from log_monitoring_service import run_log_monitoring_cycle
        result = run_log_monitoring_cycle(was_manual=True)
        return jsonify({
            "success": True,
            "result": result
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@log_monitoring_bp.route('/api/log-monitoring/issues')
@login_required
def api_log_monitoring_issues():
    """Get all log monitoring issues with filtering support."""
    try:
        from models import LogMonitoringIssue, LogMonitoringRun
        
        status_filter = request.args.get('status', None)
        severity_filter = request.args.get('severity', None)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        
        query = LogMonitoringIssue.query.order_by(LogMonitoringIssue.detected_at.desc())
        
        if status_filter and status_filter != 'all':
            query = query.filter(LogMonitoringIssue.status == status_filter)
        
        if severity_filter and severity_filter != 'all':
            query = query.filter(LogMonitoringIssue.severity == severity_filter)
        
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        issues = [{
            'id': issue.id,
            'run_id': issue.run_id,
            'detected_at': issue.detected_at.isoformat() if issue.detected_at else None,
            'pattern_name': issue.pattern_name,
            'category': issue.category,
            'severity': issue.severity,
            'description': issue.description,
            'occurrences': issue.occurrences,
            'status': issue.status,
            'resolution_action': issue.resolution_action,
            'resolution_summary': issue.resolution_summary,
            'resolved_at': issue.resolved_at.isoformat() if issue.resolved_at else None,
            'resolved_by': issue.resolved_by
        } for issue in pagination.items]
        
        total_count = LogMonitoringIssue.query.count()
        auto_fixed_count = LogMonitoringIssue.query.filter_by(status='auto_fixed').count()
        escalated_count = LogMonitoringIssue.query.filter_by(status='escalated').count()
        resolved_count = LogMonitoringIssue.query.filter_by(status='resolved').count()
        
        return jsonify({
            "success": True,
            "issues": issues,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "has_next": pagination.has_next,
                "has_prev": pagination.has_prev
            },
            "counts": {
                "total": total_count,
                "auto_fixed": auto_fixed_count,
                "escalated": escalated_count,
                "resolved": resolved_count
            }
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@log_monitoring_bp.route('/api/log-monitoring/issues/<int:issue_id>')
@login_required
def api_log_monitoring_issue_detail(issue_id):
    """Get detailed information about a specific issue."""
    try:
        from models import LogMonitoringIssue
        
        issue = LogMonitoringIssue.query.get_or_404(issue_id)
        
        return jsonify({
            "success": True,
            "issue": {
                'id': issue.id,
                'run_id': issue.run_id,
                'detected_at': issue.detected_at.isoformat() if issue.detected_at else None,
                'pattern_name': issue.pattern_name,
                'category': issue.category,
                'severity': issue.severity,
                'description': issue.description,
                'occurrences': issue.occurrences,
                'sample_log': issue.sample_log,
                'status': issue.status,
                'resolution_action': issue.resolution_action,
                'resolution_summary': issue.resolution_summary,
                'resolved_at': issue.resolved_at.isoformat() if issue.resolved_at else None,
                'resolved_by': issue.resolved_by
            }
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@log_monitoring_bp.route('/api/log-monitoring/issues/<int:issue_id>/resolve', methods=['POST'])
@login_required
def api_log_monitoring_resolve_issue(issue_id):
    """Manually resolve an escalated issue."""
    try:
        from models import LogMonitoringIssue
        
        issue = LogMonitoringIssue.query.get_or_404(issue_id)
        
        if issue.status not in ['escalated', 'detected']:
            return jsonify({
                "success": False,
                "error": "Only escalated or detected issues can be manually resolved"
            }), 400
        
        data = request.get_json() or {}
        resolution_notes = data.get('resolution_notes', 'Manually resolved')
        
        issue.mark_resolved(
            resolver_email=current_user.email,
            resolution_notes=resolution_notes
        )
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Issue #{issue_id} marked as resolved"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@log_monitoring_bp.route('/api/log-monitoring/runs')
@login_required
def api_log_monitoring_runs():
    """Get monitoring runs from database for persistence across restarts."""
    try:
        from models import LogMonitoringRun
        
        limit = request.args.get('limit', 20, type=int)
        
        runs = LogMonitoringRun.query.order_by(LogMonitoringRun.run_time.desc()).limit(limit).all()
        
        return jsonify({
            "success": True,
            "runs": [{
                'id': run.id,
                'timestamp': run.run_time.isoformat() if run.run_time else None,
                'logs_analyzed': run.logs_analyzed,
                'issues_found': run.issues_found,
                'auto_fixed': run.issues_auto_fixed,
                'escalated': run.issues_escalated,
                'status': run.status,
                'was_manual': run.was_manual,
                'execution_time_ms': run.execution_time_ms
            } for run in runs]
        })
    except Exception as e:
        from log_monitoring_service import get_log_monitor
        monitor = get_log_monitor()
        limit = request.args.get('limit', 10, type=int)
        return jsonify({
            "success": True,
            "runs": monitor.get_history(limit),
            "source": "memory"
        })

@log_monitoring_bp.route('/api/feedback', methods=['POST'])
@login_required
def api_submit_feedback():
    """Submit user feedback via email."""
    try:
        data = request.get_json()
        feedback_type = data.get('type', 'other')
        message = data.get('message', '')
        page = data.get('page', 'Unknown')
        user = data.get('user', 'Unknown')
        
        type_labels = {
            'feature': '💡 Feature Enhancement Idea',
            'bug': '🐛 Bug Report',
            'question': '❓ Question About System',
            'other': '📝 Other Feedback'
        }
        type_label = type_labels.get(feedback_type, '📝 Other Feedback')
        
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content
        
        sg_api_key = os.environ.get('SENDGRID_API_KEY')
        admin_email = os.environ.get('ADMIN_EMAIL', 'kroots@myticas.com')
        
        if sg_api_key:
            sg = SendGridAPIClient(sg_api_key)
            
            email_content = f"""
Scout Genius™ User Feedback Received

Type: {type_label}
From: {user}
Page: {page}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Message:
{message}

---
This feedback was submitted via the Scout Genius™ Feedback system.
            """
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #1e3a5f 0%, #0d2847 100%); padding: 20px; border-radius: 8px;">
                    <h2 style="color: #60a5fa; margin: 0;">📬 Scout Genius™ Feedback</h2>
                </div>
                <div style="padding: 20px; background: #f8fafc; border-radius: 0 0 8px 8px;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>Type:</strong></td><td style="padding: 8px 0;">{type_label}</td></tr>
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>From:</strong></td><td style="padding: 8px 0;">{user}</td></tr>
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>Page:</strong></td><td style="padding: 8px 0;">{page}</td></tr>
                        <tr><td style="padding: 8px 0; color: #64748b;"><strong>Time:</strong></td><td style="padding: 8px 0;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                    </table>
                    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                    <h3 style="color: #1e3a5f; margin-bottom: 10px;">Message:</h3>
                    <div style="background: white; padding: 15px; border-radius: 6px; border: 1px solid #e2e8f0;">
                        {message.replace(chr(10), '<br>')}
                    </div>
                </div>
            </div>
            """
            
            mail = Mail(
                from_email=Email("noreply@lyntrix.ai", "Scout Genius Feedback"),
                to_emails=To(admin_email),
                subject=f"[Scout Genius Feedback] {type_label} from {user}",
                plain_text_content=Content("text/plain", email_content),
                html_content=Content("text/html", html_content)
            )
            
            response = sg.send(mail)
            logger.info(f"Feedback email sent: {response.status_code}")
        
        logger.info(f"User Feedback - Type: {feedback_type}, User: {user}, Page: {page}")
        
        return jsonify({"success": True, "message": "Feedback submitted successfully"})
        
    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
