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
    builtin_tasks = [t for t in tasks if t.config_json and 'builtin_key' in (t.config_json or '')]

    # Category grouping for Built-in Tools grid — controls section labels and ordering.
    # Keeps the grid scannable as more tools are added over time.
    BUILTIN_CATEGORIES = {
        'Quality Assurance': [
            'screening_audit',
        ],
        'Data Quality': [
            'cleanup_duplicate_notes',
            'cleanup_ai_notes',
            'email_extractor',
            'resume_reparser',
            'duplicate_merge_scan',
        ],
        'Reporting': [
            'export_qualified',
            'find_zero_match',
        ],
        'Integration': [
            'retry_recruiter_notifications',
            'salesrep_sync',
            'update_field_bulk',
        ],
    }

    # Build an ordered list of (category_label, task) tuples for the template
    # so Jinja2 can render section headers inline with the grid.
    key_to_category = {k: cat for cat, keys in BUILTIN_CATEGORIES.items() for k in keys}
    categorized = []
    seen_categories = set()
    uncategorized_label = 'Other'
    for task in builtin_tasks:
        try:
            bk = json.loads(task.config_json or '{}').get('builtin_key', '')
        except Exception:
            bk = ''
        cat = key_to_category.get(bk, uncategorized_label)
        if cat not in seen_categories:
            seen_categories.add(cat)
            categorized.append({'type': 'header', 'label': cat})
        categorized.append({'type': 'task', 'task': task})

    try:
        from models import VettingConfig
        row = VettingConfig.query.filter_by(setting_key='auto_reassign_owner_enabled').first()
        owner_reassign_enabled = row and row.setting_value == 'true'
        row2 = VettingConfig.query.filter_by(setting_key='api_user_ids').first()
        api_user_ids = row2.setting_value if (row2 and row2.setting_value) else ''
        row3 = VettingConfig.query.filter_by(setting_key='reassign_owner_note_enabled').first()
        reassign_owner_note_enabled = (row3 is None) or (row3.setting_value != 'false')
    except Exception:
        owner_reassign_enabled = False
        api_user_ids = ''
        reassign_owner_note_enabled = True

    from flask import make_response
    resp = make_response(render_template('automations.html',
                           active_page='automations',
                           tasks=builtin_tasks,
                           categorized=categorized,
                           owner_reassign_enabled=owner_reassign_enabled,
                           api_user_ids=api_user_ids,
                           reassign_owner_note_enabled=reassign_owner_note_enabled))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


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
        'created_at': t.created_at.isoformat(),
        'builtin_key': json.loads(t.config_json).get('builtin_key') if t.config_json else None,
    } for t in tasks])


@automations_bp.route('/automations/run-builtin', methods=['POST'])
@login_required
def automation_run_builtin():
    _require_admin()
    from automation_service import AutomationService, LONG_RUNNING_BUILTINS
    service = AutomationService()

    data = request.get_json() or {}
    name = data.get('name')
    params = data.get('params', {})
    task_id = data.get('task_id')

    if not name:
        return jsonify({'error': 'Automation name is required'}), 400

    if name in LONG_RUNNING_BUILTINS:
        result = service.run_builtin_background(name, params, task_id=task_id)
        return jsonify(result)
    else:
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


@automations_bp.route('/automations/run-history')
@login_required
def automation_run_history():
    _require_admin()
    from automation_service import AutomationService
    service = AutomationService()
    limit = request.args.get('limit', 50, type=int)
    logs = service.get_all_logs(limit=min(limit, 200))
    return jsonify([{
        'id': l.id,
        'task_id': l.automation_task_id,
        'status': l.status,
        'message': l.message,
        'details': json.loads(l.details_json) if l.details_json else None,
        'created_at': l.created_at.isoformat()
    } for l in logs])


@automations_bp.route('/automations/builtin-status/<int:task_id>')
@login_required
def builtin_status(task_id):
    _require_admin()
    from models import AutomationLog, AutomationTask
    task = AutomationTask.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    latest_log = AutomationLog.query.filter_by(
        automation_task_id=task_id
    ).order_by(AutomationLog.created_at.desc()).first()

    if latest_log:
        return jsonify({
            'status': 'complete',
            'log_status': latest_log.status,
            'message': latest_log.message,
            'details': json.loads(latest_log.details_json) if latest_log.details_json else None,
            'created_at': latest_log.created_at.isoformat(),
            'task_status': task.status,
            'run_count': task.run_count,
            'last_run_at': task.last_run_at.isoformat() if task.last_run_at else None,
        })
    else:
        return jsonify({
            'status': 'pending',
            'task_status': task.status,
            'run_count': task.run_count,
        })


VISIBLE_JOBS = {
    'process_bullhorn_monitors': 'Tearsheet Monitor',
    'candidate_vetting_cycle': 'AI Candidate Screening',
    'vetting_health_check': 'Vetting Health Check',
    'automated_upload': 'Automated Upload',
    'salesrep_sync': 'Sales Rep Sync',
    'linkedin_source_cleanup': 'LinkedIn Source Cleanup',
    'reference_number_refresh': 'Reference Number Refresh',
    'enforce_tearsheet_jobs_public': 'Enforce Jobs Public',
    'requirements_maintenance': 'Requirements Maintenance',
    'duplicate_merge_check': 'Duplicate Candidate Merge',
    'owner_reassignment': 'Owner Reassignment (5 min)',
    'owner_reassignment_daily': 'Owner Reassignment (Daily Sweep)',
}

PROTECTED_JOBS = {'process_bullhorn_monitors', 'candidate_vetting_cycle', 'vetting_health_check'}

INTERNAL_JOBS = {
    'check_monitor_health', 'environment_monitoring', 'refresh_active_job_ids',
    'activity_cleanup', 'log_monitoring', 'email_parsing_timeout_cleanup',
    'data_retention_cleanup',
}


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

                last_result_text = None
                if job_id == 'enforce_tearsheet_jobs_public':
                    try:
                        result_raw = GlobalSettings.get_value('enforce_public_last_result')
                        if result_raw:
                            result_data = _json.loads(result_raw)
                            count = result_data.get('succeeded', 0)
                            ids = result_data.get('sample_ids', [])
                            if count > 0:
                                id_str = ', '.join(str(i) for i in ids[:5])
                                last_result_text = f"{count} job(s) set to public (IDs: {id_str})"
                            else:
                                last_result_text = "All jobs already public"
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
                    'last_result_text': last_result_text,
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

        logger.info(f"Scheduled job paused: {job_id}")
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

        logger.info(f"Scheduled job resumed: {job_id}")
        return jsonify({'success': True, 'job_id': job_id, 'paused': False})
    except Exception as e:
        logger.error(f"Failed to resume job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@automations_bp.route('/automations/cleanup-settings', methods=['GET'])
@login_required
def cleanup_settings_get():
    _require_admin()
    from models import GlobalSettings
    enabled = GlobalSettings.get_value('candidate_cleanup_enabled', 'false').lower() == 'true'
    batch_size = GlobalSettings.get_value('candidate_cleanup_batch_size', '50')
    return jsonify({'enabled': enabled, 'batch_size': batch_size})


@automations_bp.route('/automations/incomplete-rescreen-settings', methods=['GET'])
@login_required
def incomplete_rescreen_settings_get():
    _require_admin()
    from models import GlobalSettings
    enabled = GlobalSettings.get_value('incomplete_rescreen_enabled', 'false').lower() == 'true'
    return jsonify({'enabled': enabled})


@automations_bp.route('/automations/incomplete-rescreen-settings', methods=['POST'])
@login_required
def incomplete_rescreen_settings_save():
    _require_admin()
    from models import GlobalSettings
    from extensions import db
    data = request.get_json() or {}
    if 'enabled' in data:
        GlobalSettings.set_value('incomplete_rescreen_enabled', 'true' if data['enabled'] else 'false')
    logger.info(f"Incomplete rescreen settings updated: {json.dumps(data)}")
    return jsonify({'success': True})


@automations_bp.route('/automations/cleanup-settings', methods=['POST'])
@login_required
def cleanup_settings_save():
    _require_admin()
    from models import GlobalSettings
    from extensions import db
    data = request.get_json() or {}
    if 'enabled' in data:
        GlobalSettings.set_value('candidate_cleanup_enabled', 'true' if data['enabled'] else 'false')
    if 'batch_size' in data:
        try:
            size = max(1, min(500, int(data['batch_size'])))
            GlobalSettings.set_value('candidate_cleanup_batch_size', str(size))
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid batch_size'}), 400
    logger.info(f"Candidate cleanup settings updated: {json.dumps(data)}")
    return jsonify({'success': True})


@automations_bp.route('/automations/owner-reassign', methods=['POST'])
@login_required
def owner_reassign_action():
    _require_admin()
    data = request.get_json() or {}
    action = data.get('action')

    if action == 'ownership_toggle':
        new_value = data.get('enabled', False)
        try:
            from models import VettingConfig
            from extensions import db as _db
            row = VettingConfig.query.filter_by(setting_key='auto_reassign_owner_enabled').first()
            if row:
                row.setting_value = 'true' if new_value else 'false'
            else:
                row = VettingConfig(
                    setting_key='auto_reassign_owner_enabled',
                    setting_value='true' if new_value else 'false',
                )
                _db.session.add(row)
            _db.session.commit()
            return jsonify({'success': True, 'enabled': new_value})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    elif action == 'ownership_save_config':
        try:
            from models import VettingConfig
            from extensions import db as _db
            api_user_ids_raw = (data.get('api_user_ids') or '').strip()
            note_enabled = bool(data.get('reassign_owner_note_enabled', True))
            sanitized_ids = ','.join(
                p.strip() for p in api_user_ids_raw.split(',') if p.strip().isdigit()
            )
            if api_user_ids_raw and not sanitized_ids:
                return jsonify({'success': False, 'error': 'API user IDs must be numeric (comma-separated).'})
            for key, value in [('api_user_ids', sanitized_ids),
                               ('reassign_owner_note_enabled', 'true' if note_enabled else 'false')]:
                row = VettingConfig.query.filter_by(setting_key=key).first()
                if row:
                    row.setting_value = value
                else:
                    row = VettingConfig(setting_key=key, setting_value=value)
                    _db.session.add(row)
            _db.session.commit()
            return jsonify({'success': True, 'api_user_ids': sanitized_ids})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    elif action == 'ownership_preview':
        try:
            from tasks.owner_reassignment import preview_reassign_candidates
            result = preview_reassign_candidates(limit=5)
            return jsonify({'success': True, **result})
        except Exception as e:
            logger.error(f"ownership_preview error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)})

    elif action == 'ownership_run_live':
        import threading
        try:
            from tasks.owner_reassignment import run_owner_reassignment
            thread = threading.Thread(
                target=run_owner_reassignment,
                name='owner_reassignment_live_batch',
                daemon=True,
            )
            thread.start()
            logger.info("owner_reassignment: live batch started in background thread")
            return jsonify({'success': True, 'started': True,
                            'message': 'Live batch started in background. Check the scheduled jobs grid or production logs for progress.'})
        except Exception as e:
            logger.error(f"ownership_run_live error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)})

    return jsonify({'success': False, 'error': 'Unknown action'}), 400
