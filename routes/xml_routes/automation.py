import os
import json
import logging
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from flask import jsonify
from flask_login import login_required
from extensions import db
from routes.xml_routes import xml_routes_bp

logger = logging.getLogger(__name__)


def _manual_upload_all_feeds():
    """Generate and upload the full XML feed set for a manual trigger."""
    from models import GlobalSettings
    from ftp_service import FTPService
    from simplified_xml_generator import SimplifiedXMLGenerator
    from tasks.xml_feeds import _upload_single_file
    from feeds.feed_config import (
        CHANNEL_FEEDS,
        SOURCE_LINKEDIN,
        V2_FILENAME,
        V2_FILENAME_DEV,
    )

    sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
    sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
    sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
    sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
    sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

    if not (sftp_hostname and sftp_hostname.setting_value and
            sftp_username and sftp_username.setting_value and
            sftp_password and sftp_password.setting_value):
        return {
            'success': False,
            'error': 'SFTP credentials not configured. Please fill in hostname, username, and password.'
        }

    generator = SimplifiedXMLGenerator(db=db)
    v2_xml, v2_stats = generator.generate_fresh_xml(source_channel=SOURCE_LINKEDIN)

    channel_results = {}
    for feed_cfg in CHANNEL_FEEDS:
        key = feed_cfg['key']
        xml_content, stats = generator.generate_fresh_xml(
            tearsheet_ids=feed_cfg['tearsheet_ids'],
            source_channel=feed_cfg['source_channel'],
            allow_empty=feed_cfg.get('allow_empty', False),
        )
        channel_results[key] = {
            'xml': xml_content,
            'stats': stats,
            'filenames': {
                'production': feed_cfg['filename'],
                'development': feed_cfg.get('filename_dev', feed_cfg['filename']),
            },
        }

    try:
        port_value = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222
    except ValueError:
        port_value = 2222

    target_directory = sftp_directory.setting_value if sftp_directory else "/"
    ftp_service = FTPService(
        hostname=sftp_hostname.setting_value,
        username=sftp_username.setting_value,
        password=sftp_password.setting_value,
        target_directory=target_directory,
        port=port_value,
        use_sftp=True
    )

    current_env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
    if current_env not in ['production', 'development']:
        logger.error("Invalid environment '%s' - defaulting to development for safety", current_env)
        current_env = 'development'

    v2_filename = V2_FILENAME if current_env == 'production' else V2_FILENAME_DEV
    upload_errors = []
    uploaded_files = []

    log_context = SimpleNamespace(logger=logger)

    v2_ok, v2_err = _upload_single_file(ftp_service, v2_xml, v2_filename, log_context)
    if v2_ok:
        uploaded_files.append({
            'key': 'v2',
            'filename': v2_filename,
            'job_count': v2_stats['job_count'],
            'xml_size_bytes': v2_stats['xml_size_bytes'],
        })
    else:
        upload_errors.append(f"v2: {v2_err}")

    for key, result in channel_results.items():
        remote_filename = result['filenames'][current_env]
        ok, err = _upload_single_file(ftp_service, result['xml'], remote_filename, log_context)
        if ok:
            uploaded_files.append({
                'key': key,
                'filename': remote_filename,
                'job_count': result['stats']['job_count'],
                'xml_size_bytes': result['stats']['xml_size_bytes'],
            })
        else:
            upload_errors.append(f"{key}: {err}")

    return {
        'success': not upload_errors,
        'environment': current_env,
        'target_directory': target_directory,
        'uploaded_files': uploaded_files,
        'upload_errors': upload_errors,
        'v2_stats': v2_stats,
        'channel_stats': {
            key: result['stats'] for key, result in channel_results.items()
        },
    }


@xml_routes_bp.route('/automation-status')
@login_required
def automation_status():
    """Get current automation status — DB-first for enabled state, scheduler for precise timing"""
    try:
        from models import GlobalSettings

        UPLOAD_INTERVAL_MINUTES = 30
        upload_interval = f"{UPLOAD_INTERVAL_MINUTES} minutes"

        automation_setting = GlobalSettings.query.filter_by(setting_key='automated_uploads_enabled').first()
        db_setting_enabled = automation_setting and automation_setting.setting_value == 'true'

        last_upload_setting = GlobalSettings.query.filter_by(setting_key='last_sftp_upload_time').first()
        last_upload_raw = last_upload_setting.setting_value if last_upload_setting else None
        last_upload_time = last_upload_raw if last_upload_raw else "No uploads yet"

        next_upload_time = None
        next_upload_iso = None
        next_upload_timestamp = None

        if db_setting_enabled:
            next_upload_setting = GlobalSettings.query.filter_by(setting_key='next_sftp_upload_time').first()
            if next_upload_setting and next_upload_setting.setting_value:
                try:
                    next_dt = None
                    for fmt in ('%Y-%m-%d %H:%M:%S UTC', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                        try:
                            next_dt = datetime.strptime(next_upload_setting.setting_value.strip(), fmt).replace(tzinfo=timezone.utc)
                            break
                        except ValueError:
                            continue
                    if next_dt:
                        now_utc = datetime.now(timezone.utc)
                        if next_dt >= now_utc:
                            next_upload_time = next_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                            next_upload_iso = next_dt.isoformat()
                            next_upload_timestamp = int(next_dt.timestamp() * 1000)
                except Exception:
                    pass

            if not next_upload_time:
                try:
                    from app import scheduler
                    job = scheduler.get_job('automated_upload')
                    if job and job.next_run_time:
                        next_run = job.next_run_time
                        next_upload_time = next_run.strftime('%Y-%m-%d %H:%M:%S UTC')
                        next_upload_iso = next_run.isoformat()
                        next_upload_timestamp = int(next_run.timestamp() * 1000)
                except Exception:
                    pass

            if not next_upload_time:
                if last_upload_raw:
                    try:
                        last_dt = None
                        for fmt in ('%Y-%m-%d %H:%M:%S UTC', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                            try:
                                last_dt = datetime.strptime(last_upload_raw.strip(), fmt).replace(tzinfo=timezone.utc)
                                break
                            except ValueError:
                                continue
                        if last_dt:
                            now_utc = datetime.now(timezone.utc)
                            next_dt = last_dt + timedelta(minutes=UPLOAD_INTERVAL_MINUTES)
                            if next_dt < now_utc:
                                next_dt = now_utc + timedelta(minutes=1)
                            next_upload_time = next_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                            next_upload_iso = next_dt.isoformat()
                            next_upload_timestamp = int(next_dt.timestamp() * 1000)
                    except Exception:
                        pass

            if not next_upload_time:
                next_upload_time = 'Pending first run'

        automation_enabled = db_setting_enabled
        status = 'Active' if db_setting_enabled else 'Disabled'

        dual_feed_info = None
        try:
            dual_feed_setting = GlobalSettings.query.filter_by(setting_key='dual_feed_last_result').first()
            if dual_feed_setting and dual_feed_setting.setting_value:
                dual_feed_info = json.loads(dual_feed_setting.setting_value)
        except Exception:
            pass

        return jsonify({
            'automation_enabled': automation_enabled,
            'db_setting_enabled': db_setting_enabled,
            'next_upload_time': next_upload_time,
            'next_upload_iso': next_upload_iso,
            'next_upload_timestamp': next_upload_timestamp,
            'last_upload_time': last_upload_time,
            'upload_interval': upload_interval,
            'status': status,
            'dual_feed': dual_feed_info
        })

    except Exception as e:
        logger.error(f"Error getting automation status: {str(e)}")
        return jsonify({'error': 'Failed to get automation status'}), 500


@xml_routes_bp.route('/test-upload', methods=['POST'])
@login_required
def manual_test_upload():
    """Manual upload testing for dev environment"""
    try:
        from models import GlobalSettings

        logger.info("🧪 Manual test upload initiated")

        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP not enabled in settings',
            })

        upload_result = _manual_upload_all_feeds()

        if not upload_result.get('success'):
            return jsonify({
                'success': False,
                'error': '; '.join(upload_result.get('upload_errors', [])) or upload_result.get('error', 'Upload failed'),
                'uploaded_files': upload_result.get('uploaded_files', []),
                'environment': upload_result.get('environment'),
            }), 500

        return jsonify({
            'success': True,
            'message': 'Test upload completed',
            'uploaded_files': upload_result['uploaded_files'],
            'destination': upload_result['target_directory'],
            'environment': upload_result['environment'],
        })

    except Exception as e:
        logger.error(f"Manual test upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        }), 500


@xml_routes_bp.route('/manual-upload-now', methods=['POST'])
@login_required
def manual_upload_now():
    """Manually trigger XML generation and SFTP upload"""
    try:
        from models import GlobalSettings

        logger.info("📤 Manual upload triggered by user")

        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP is not enabled. Please enable it in settings first.'
            })

        upload_result = _manual_upload_all_feeds()

        if upload_result.get('success'):
            logger.info("✅ Manual upload successful: %s", ', '.join(
                f["filename"] for f in upload_result['uploaded_files']
            ))
            return jsonify({
                'success': True,
                'message': 'Successfully uploaded all XML feeds',
                'uploaded_files': upload_result['uploaded_files'],
                'environment': upload_result['environment'],
                'destination': upload_result['target_directory'],
            })

        logger.error("❌ Manual upload failed: %s", '; '.join(upload_result.get('upload_errors', [])))
        return jsonify({
            'success': False,
            'error': '; '.join(upload_result.get('upload_errors', [])) or upload_result.get('error', 'Upload failed'),
            'uploaded_files': upload_result.get('uploaded_files', []),
            'environment': upload_result.get('environment'),
        }), 500

    except Exception as e:
        logger.error(f"❌ Manual upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        })
