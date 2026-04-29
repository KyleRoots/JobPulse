import os
import json
import tempfile
import logging
from datetime import datetime, timezone, timedelta
from flask import request, jsonify
from flask_login import login_required
from extensions import db
from routes.xml_routes import xml_routes_bp

logger = logging.getLogger(__name__)


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
        from tasks import automated_upload

        logger.info("🧪 Manual test upload initiated")

        from simplified_xml_generator import SimplifiedXMLGenerator
        generator = SimplifiedXMLGenerator(db=db)
        xml_content, stats = generator.generate_fresh_xml()

        logger.info(f"📊 Generated test XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP not enabled in settings',
                'job_count': stats['job_count'],
                'xml_size': stats['xml_size_bytes']
            })

        upload_result = automated_upload()

        return jsonify({
            'success': True,
            'message': 'Test upload completed',
            'job_count': stats['job_count'],
            'xml_size': stats['xml_size_bytes'],
            'destination': 'configured SFTP directory',
            'note': 'Upload attempted - check logs for detailed results'
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
        from ftp_service import FTPService

        logger.info("📤 Manual upload triggered by user")

        sftp_enabled = GlobalSettings.query.filter_by(setting_key='sftp_enabled').first()
        if not (sftp_enabled and sftp_enabled.setting_value == 'true'):
            return jsonify({
                'success': False,
                'error': 'SFTP is not enabled. Please enable it in settings first.'
            })

        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()

        if not (sftp_hostname and sftp_hostname.setting_value and
                sftp_username and sftp_username.setting_value and
                sftp_password and sftp_password.setting_value):
            return jsonify({
                'success': False,
                'error': 'SFTP credentials not configured. Please fill in hostname, username, and password.'
            })

        from simplified_xml_generator import SimplifiedXMLGenerator
        generator = SimplifiedXMLGenerator(db=db)
        xml_content, stats = generator.generate_fresh_xml()

        logger.info(f"📊 Generated fresh XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")

        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-8')
        temp_file.write(xml_content)
        temp_file.close()

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

        upload_result = ftp_service.upload_file(temp_file.name, 'myticas-job-feed-v2.xml')

        try:
            os.remove(temp_file.name)
        except Exception:
            pass

        if isinstance(upload_result, dict):
            if upload_result.get('success'):
                logger.info(f"✅ Manual upload successful: {upload_result.get('message', 'File uploaded')}")
                return jsonify({
                    'success': True,
                    'message': f"Successfully uploaded XML with {stats['job_count']} jobs ({stats['xml_size_bytes']:,} bytes)"
                })
            else:
                logger.error(f"❌ Manual upload failed: {upload_result.get('error', 'Unknown error')}")
                return jsonify({
                    'success': False,
                    'error': upload_result.get('error', 'Upload failed')
                })
        else:
            if upload_result:
                logger.info("✅ Manual upload successful")
                return jsonify({
                    'success': True,
                    'message': f"Successfully uploaded XML with {stats['job_count']} jobs ({stats['xml_size_bytes']:,} bytes)"
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Upload failed - check SFTP settings'
                })

    except Exception as e:
        logger.error(f"❌ Manual upload error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        })
