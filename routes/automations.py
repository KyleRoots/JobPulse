import json
import logging
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required

logger = logging.getLogger(__name__)

automations_bp = Blueprint('automations', __name__)


def _is_dev_only():
    import os
    from flask import request as req
    PRODUCTION_DOMAINS = {
        'app.scoutgenius.ai', 'www.app.scoutgenius.ai',
        'jobpulse.lyntrix.ai', 'www.jobpulse.lyntrix.ai'
    }
    host = req.headers.get('X-Forwarded-Host', req.host or '').split(',')[0].strip()
    clean_host = host.split(':')[0].lower()
    if clean_host in PRODUCTION_DOMAINS:
        return False
    env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
    if os.environ.get('REPLIT_DEPLOYMENT'):
        return False
    return True


@automations_bp.route('/automations')
@login_required
def automations_dashboard():
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()
    tasks = service.get_tasks()
    chat_history = service.get_chat_history(task_id=None)
    return render_template('automations.html',
                           active_page='automations',
                           tasks=tasks,
                           chat_history=chat_history)


@automations_bp.route('/automations/chat', methods=['POST'])
@login_required
def automations_chat():
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()

    data = request.get_json()
    message = data.get('message', '').strip()
    task_id = data.get('task_id')

    if not message:
        return jsonify({'error': 'Message is required'}), 400

    result = service.chat(message, task_id=task_id)
    return jsonify(result)


@automations_bp.route('/automations/list')
@login_required
def automations_list():
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()
    tasks = service.get_tasks()
    return jsonify([{
        'id': t.id,
        'name': t.name,
        'description': t.description,
        'status': t.status,
        'type': t.automation_type,
        'schedule': t.schedule_cron,
        'last_run': t.last_run_at.isoformat() if t.last_run_at else None,
        'run_count': t.run_count,
        'created_at': t.created_at.isoformat()
    } for t in tasks])


@automations_bp.route('/automations/<int:task_id>/run', methods=['POST'])
@login_required
def automation_run(task_id):
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()

    data = request.get_json() or {}
    operation = data.get('operation')
    params = data.get('params', {})

    if not operation:
        return jsonify({'error': 'Operation is required'}), 400

    result = service.execute_bullhorn_operation(operation, params, task_id=task_id)
    return jsonify(result)


@automations_bp.route('/automations/run-builtin', methods=['POST'])
@login_required
def automation_run_builtin():
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()

    data = request.get_json() or {}
    name = data.get('name')
    params = data.get('params', {})
    task_id = data.get('task_id')

    if not name:
        return jsonify({'error': 'Automation name is required'}), 400

    result = service.run_builtin(name, params, task_id=task_id)
    return jsonify(result)


@automations_bp.route('/automations/<int:task_id>/status', methods=['POST'])
@login_required
def automation_status(task_id):
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()

    data = request.get_json() or {}
    status = data.get('status')

    if status not in ('draft', 'active', 'paused', 'completed', 'failed'):
        return jsonify({'error': 'Invalid status'}), 400

    service.update_task_status(task_id, status)
    return jsonify({'success': True})


@automations_bp.route('/automations/<int:task_id>', methods=['DELETE'])
@login_required
def automation_delete(task_id):
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()
    if service.delete_task(task_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Task not found'}), 404


@automations_bp.route('/automations/<int:task_id>/logs')
@login_required
def automation_logs(task_id):
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()
    logs = service.get_task_logs(task_id)
    return jsonify([{
        'id': l.id,
        'status': l.status,
        'message': l.message,
        'details': json.loads(l.details_json) if l.details_json else None,
        'created_at': l.created_at.isoformat()
    } for l in logs])


@automations_bp.route('/automations/<int:task_id>/chat')
@login_required
def automation_chat_history(task_id):
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    service = AutomationService()
    history = service.get_chat_history(task_id=task_id)
    return jsonify([{
        'id': c.id,
        'role': c.role,
        'content': c.content,
        'created_at': c.created_at.isoformat()
    } for c in history])


@automations_bp.route('/automations/chat/clear', methods=['POST'])
@login_required
def automations_chat_clear():
    if not _is_dev_only():
        abort(404)
    from automation_service import AutomationService
    data = request.get_json() or {}
    task_id = data.get('task_id')
    service = AutomationService()
    service.clear_chat_history(task_id=task_id)
    return jsonify({'success': True})


@automations_bp.route('/automations/scheduler-status')
@login_required
def scheduler_status():
    if not _is_dev_only():
        abort(404)
    from flask import current_app
    jobs = []
    try:
        scheduler = current_app.config.get('_scheduler')
        if not scheduler:
            from apscheduler.schedulers.background import BackgroundScheduler
            for obj_name in dir(current_app):
                obj = getattr(current_app, obj_name, None)
                if isinstance(obj, BackgroundScheduler):
                    scheduler = obj
                    break

        if not scheduler:
            import app as app_module
            scheduler = getattr(app_module, 'scheduler', None)

        if scheduler and scheduler.running:
            for job in scheduler.get_jobs():
                next_run = job.next_run_time
                jobs.append({
                    'id': job.id,
                    'name': job.name,
                    'next_run': next_run.isoformat() if next_run else None,
                    'active': next_run is not None
                })
    except Exception as e:
        logger.error(f"Failed to get scheduler status: {e}")

    return jsonify(jobs)
