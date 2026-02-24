import json
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from routes import register_admin_guard
from extensions import db

logger = logging.getLogger(__name__)
ats_monitoring_bp = Blueprint('ats_monitoring', __name__)
register_admin_guard(ats_monitoring_bp)


@ats_monitoring_bp.route('/ats-monitoring')
@login_required
def ats_monitoring_page():
    """ATS monitoring dashboard"""
    return render_template('ats_monitoring.html', active_page='ats_monitoring')


@ats_monitoring_bp.route('/api/monitors')
@login_required
def get_monitors():
    """Get all active Bullhorn monitors"""
    try:
        from models import BullhornMonitor

        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        monitor_data = []

        for monitor in monitors:
            job_count = 0
            if monitor.last_job_snapshot:
                try:
                    jobs = json.loads(monitor.last_job_snapshot)
                    job_count = len(jobs) if isinstance(jobs, list) else 0
                except:
                    job_count = 0

            monitor_data.append({
                'id': monitor.id,
                'name': monitor.name,
                'tearsheet_name': monitor.tearsheet_name,
                'tearsheet_id': monitor.tearsheet_id,
                'interval_minutes': monitor.check_interval_minutes,
                'last_check': monitor.last_check.isoformat() if monitor.last_check else None,
                'next_check': monitor.next_check.isoformat() if monitor.next_check else None,
                'job_count': job_count,
                'is_active': monitor.is_active
            })

        return jsonify(monitor_data)
    except Exception as e:
        logger.error(f"Error fetching monitors: {str(e)}")
        return jsonify([]), 500


@ats_monitoring_bp.route('/api/monitors/<int:monitor_id>', methods=['DELETE'])
@login_required
def delete_monitor(monitor_id):
    """Delete a Bullhorn monitor"""
    try:
        from models import BullhornMonitor, BullhornActivity

        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        monitor_name = monitor.name

        BullhornActivity.query.filter_by(monitor_id=monitor_id).delete()

        db.session.delete(monitor)
        db.session.commit()

        logger.info(f"Deleted monitor: {monitor_name}")
        return jsonify({'success': True, 'message': f'Monitor "{monitor_name}" deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting monitor: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@ats_monitoring_bp.route('/api/monitors/<int:monitor_id>/toggle', methods=['POST'])
@login_required
def toggle_monitor(monitor_id):
    """Toggle monitor active status"""
    try:
        from models import BullhornMonitor

        monitor = BullhornMonitor.query.get_or_404(monitor_id)
        monitor.is_active = not monitor.is_active

        if monitor.is_active:
            monitor.calculate_next_check()

        db.session.commit()

        status = "activated" if monitor.is_active else "deactivated"
        logger.info(f"Monitor {monitor.name} {status}")

        return jsonify({
            'success': True,
            'message': f'Monitor "{monitor.name}" {status}',
            'is_active': monitor.is_active,
            'next_check': monitor.next_check.isoformat() if monitor.next_check else None
        })
    except Exception as e:
        logger.error(f"Error toggling monitor: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@ats_monitoring_bp.route('/api/activities')
@login_required
def get_activities():
    """Get recent Bullhorn activities"""
    try:
        from models import BullhornActivity, BullhornMonitor

        activities = BullhornActivity.query.order_by(BullhornActivity.created_at.desc()).limit(50).all()

        monitor_ids = {a.monitor_id for a in activities if a.monitor_id}
        monitor_map = {}
        if monitor_ids:
            monitors = BullhornMonitor.query.filter(BullhornMonitor.id.in_(monitor_ids)).all()
            monitor_map = {m.id: m.name for m in monitors}

        activity_data = []
        for activity in activities:
            if activity.monitor_id:
                monitor_name = monitor_map.get(activity.monitor_id, "Unknown")
            else:
                monitor_name = "System"

            details = activity.details or ''
            if len(details) > 200:
                details = details[:200] + '...'

            activity_data.append({
                'id': activity.id,
                'timestamp': activity.created_at.isoformat(),
                'monitor_name': monitor_name,
                'activity_type': activity.activity_type,
                'details': details
            })

        return jsonify(activity_data)
    except Exception as e:
        logger.error(f"Error fetching activities: {str(e)}")
        return jsonify([]), 500


@ats_monitoring_bp.route('/api/system/health')
@login_required
def system_health():
    """Get system health status"""
    try:
        from models import BullhornMonitor
        from app import scheduler

        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        current_time = datetime.utcnow()

        healthy_monitors = 0
        overdue_monitors = 0

        for monitor in monitors:
            if monitor.next_check and monitor.next_check < current_time - timedelta(minutes=10):
                overdue_monitors += 1
            else:
                healthy_monitors += 1

        status = "healthy" if overdue_monitors == 0 else "warning"

        return jsonify({
            'status': status,
            'total_monitors': len(monitors),
            'healthy_monitors': healthy_monitors,
            'overdue_monitors': overdue_monitors,
            'scheduler_status': 'running' if scheduler.running else 'stopped'
        })
    except Exception as e:
        logger.error(f"Error getting system health: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@ats_monitoring_bp.route('/api/monitors', methods=['POST'])
@login_required
def create_monitor():
    """Create a new Bullhorn monitor"""
    try:
        from models import BullhornMonitor

        data = request.get_json()

        if not data.get('name') or not data.get('tearsheet_name'):
            return jsonify({'success': False, 'error': 'Name and tearsheet name are required'}), 400

        monitor = BullhornMonitor(
            name=data['name'],
            tearsheet_name=data['tearsheet_name'],
            tearsheet_id=data.get('tearsheet_id', 0),
            interval_minutes=data.get('interval_minutes', 5),
            is_active=True
        )
        monitor.calculate_next_check()

        db.session.add(monitor)
        db.session.commit()

        logger.info(f"Created new monitor: {monitor.name}")

        return jsonify({
            'success': True,
            'message': f'Monitor "{monitor.name}" created successfully',
            'monitor_id': monitor.id
        })
    except Exception as e:
        logger.error(f"Error creating monitor: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
