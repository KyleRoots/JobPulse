import json
import logging
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

automations_bp = Blueprint('automations', __name__)


def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


@automations_bp.route('/automations')
@login_required
def automations_dashboard():
    _require_admin()
    from automation_service import AutomationService
    service = AutomationService()
    tasks = service.get_tasks()
    chat_history = service.get_chat_history(task_id=None)
    from flask import make_response
    resp = make_response(render_template('automations.html',
                           active_page='automations',
                           tasks=tasks,
                           chat_history=chat_history))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@automations_bp.route('/automations/chat', methods=['POST'])
@login_required
def automations_chat():
    _require_admin()
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
    _require_admin()
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
    _require_admin()
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
    _require_admin()
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
    _require_admin()
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
    _require_admin()
    from automation_service import AutomationService
    service = AutomationService()
    if service.delete_task(task_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Task not found'}), 404


@automations_bp.route('/automations/<int:task_id>/logs')
@login_required
def automation_logs(task_id):
    _require_admin()
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
    _require_admin()
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
    _require_admin()
    from automation_service import AutomationService
    data = request.get_json() or {}
    task_id = data.get('task_id')
    service = AutomationService()
    service.clear_chat_history(task_id=task_id)
    return jsonify({'success': True})


VISIBLE_JOBS = {
    'process_bullhorn_monitors': 'Tearsheet Monitor',
    'candidate_vetting_cycle': 'AI Candidate Vetting',
    'vetting_health_check': 'Vetting Health Check',
    'automated_upload': 'Automated Upload',
    'salesrep_sync': 'Sales Rep Sync',
    'linkedin_source_cleanup': 'LinkedIn Source Cleanup',
    'reference_number_refresh': 'Reference Number Refresh',
}

PROTECTED_JOBS = {'process_bullhorn_monitors', 'candidate_vetting_cycle', 'vetting_health_check'}


def _trigger_description(job):
    try:
        from apscheduler.triggers.interval import IntervalTrigger as APSIntervalTrigger
        trigger = job.trigger
        if isinstance(trigger, APSIntervalTrigger):
            seconds = int(trigger.interval.total_seconds())
            if seconds >= 86400:
                h = seconds // 3600
                return f"every {h} hours"
            elif seconds >= 7200:
                h = seconds // 3600
                return f"every {h} hours"
            elif seconds >= 3600:
                return "every hour"
            elif seconds >= 120:
                return f"every {seconds // 60} minutes"
            elif seconds >= 60:
                return "every minute"
            else:
                return f"every {seconds}s"
    except Exception:
        pass
    return "scheduled"


def _get_scheduler():
    import app as app_module
    return getattr(app_module, 'scheduler', None)


@automations_bp.route('/automations/scheduler-status')
@login_required
def scheduler_status():
    _require_admin()
    import json as _json
    from models import GlobalSettings

    try:
        paused_raw = GlobalSettings.get_value('scheduler_paused_jobs', '[]')
        paused_ids = set(_json.loads(paused_raw))
    except Exception:
        paused_ids = set()

    jobs = []
    try:
        scheduler = _get_scheduler()
        if scheduler and scheduler.running:
            job_map = {j.id: j for j in scheduler.get_jobs()}
            for job_id, display_name in VISIBLE_JOBS.items():
                job = job_map.get(job_id)
                next_run = None
                paused = job_id in paused_ids

                if job:
                    next_run = job.next_run_time

                last_run_at = None
                last_run_success = None
                try:
                    raw = GlobalSettings.get_value(f'scheduler_last_run_{job_id}')
                    if raw:
                        data = _json.loads(raw)
                        last_run_at = data.get('timestamp')
                        last_run_success = data.get('success')
                except Exception:
                    pass

                jobs.append({
                    'id': job_id,
                    'name': display_name,
                    'trigger_description': _trigger_description(job) if job else 'scheduled',
                    'next_run': next_run.isoformat() if next_run else None,
                    'paused': paused,
                    'last_run_at': last_run_at,
                    'last_run_success': last_run_success,
                    'is_protected': job_id in PROTECTED_JOBS,
                    'active': job is not None and next_run is not None,
                })
    except Exception as e:
        logger.error(f"Failed to get scheduler status: {e}")

    return jsonify(jobs)


@automations_bp.route('/automations/scheduler-jobs/<job_id>/pause', methods=['POST'])
@login_required
def scheduler_job_pause(job_id):
    _require_admin()
    import json as _json
    from models import GlobalSettings

    if job_id not in VISIBLE_JOBS:
        return jsonify({'error': 'Unknown job'}), 400

    try:
        scheduler = _get_scheduler()
        if not scheduler or not scheduler.running:
            return jsonify({'error': 'Scheduler not running'}), 503

        scheduler.pause_job(job_id)

        paused_raw = GlobalSettings.get_value('scheduler_paused_jobs', '[]')
        paused_ids = list(set(_json.loads(paused_raw)) | {job_id})
        GlobalSettings.set_value('scheduler_paused_jobs', _json.dumps(paused_ids))

        logger.info(f"⏸ Scheduled job paused: {job_id}")
        return jsonify({'success': True, 'job_id': job_id, 'paused': True})
    except Exception as e:
        logger.error(f"Failed to pause job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@automations_bp.route('/automations/scheduler-jobs/<job_id>/resume', methods=['POST'])
@login_required
def scheduler_job_resume(job_id):
    _require_admin()
    import json as _json
    from models import GlobalSettings

    if job_id not in VISIBLE_JOBS:
        return jsonify({'error': 'Unknown job'}), 400

    try:
        scheduler = _get_scheduler()
        if not scheduler or not scheduler.running:
            return jsonify({'error': 'Scheduler not running'}), 503

        scheduler.resume_job(job_id)

        paused_raw = GlobalSettings.get_value('scheduler_paused_jobs', '[]')
        paused_ids = list(set(_json.loads(paused_raw)) - {job_id})
        GlobalSettings.set_value('scheduler_paused_jobs', _json.dumps(paused_ids))

        logger.info(f"▶️ Scheduled job resumed: {job_id}")
        return jsonify({'success': True, 'job_id': job_id, 'paused': False})
    except Exception as e:
        logger.error(f"Failed to resume job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500
