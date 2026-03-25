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
    """Submit user feedback as a platform support ticket."""
    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid request data"}), 400
        feedback_type = data.get('type', 'other')
        message = data.get('message', '')
        page = data.get('page', 'Unknown')

        if not message or not message.strip():
            return jsonify({"success": False, "error": "Message is required"}), 400

        category_map = {
            'bug': 'platform_bug',
            'feature': 'platform_feature',
            'question': 'platform_question',
            'other': 'platform_other',
        }
        category = category_map.get(feedback_type, 'platform_other')

        type_labels = {
            'feature': 'Feature Request',
            'bug': 'Bug Report',
            'question': 'Question',
            'other': 'Feedback',
        }
        type_label = type_labels.get(feedback_type, 'Feedback')

        subject = f"{type_label}: {message[:80]}{'...' if len(message) > 80 else ''}"
        description = f"{message}\n\nPage: {page}"

        user_company = getattr(current_user, 'company', None) or 'Myticas'
        brand = 'STSI' if user_company and 'stsi' in user_company.lower() else 'Myticas'

        from scout_support_service import ScoutSupportService
        svc = ScoutSupportService()
        ticket = svc.create_ticket(
            category=category,
            subject=subject,
            description=description,
            submitter_name=current_user.full_name if hasattr(current_user, 'full_name') and current_user.full_name else current_user.username,
            submitter_email=current_user.email,
            submitter_department=getattr(current_user, 'department', None),
            brand=brand,
            priority='medium' if feedback_type == 'bug' else 'low',
        )

        try:
            svc.process_new_ticket(ticket.id)
        except Exception as proc_err:
            logger.error(f"Platform ticket AI processing failed for {ticket.ticket_number}: {proc_err}")

        logger.info(f"Platform feedback ticket created: {ticket.ticket_number} by {current_user.email}")

        return jsonify({
            "success": True,
            "message": "Your feedback has been submitted as a support ticket.",
            "ticket_number": ticket.ticket_number,
        })

    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
