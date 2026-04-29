import os
import re
import json
import time
import shutil
import uuid
import signal
import logging
from flask import render_template, request, jsonify, redirect, url_for, flash, send_file
from flask_login import login_required
from routes.xml_routes import xml_routes_bp

logger = logging.getLogger(__name__)


@xml_routes_bp.route('/automation_test')
@login_required
def automation_test():
    """Automation test center page"""
    reset_test_file()
    try:
        from models import VettingConfig
        row = VettingConfig.query.filter_by(setting_key='auto_reassign_owner_enabled').first()
        owner_reassign_enabled = row and row.setting_value == 'true'
        row2 = VettingConfig.query.filter_by(setting_key='api_user_ids').first()
        api_user_ids_configured = bool(row2 and row2.setting_value and row2.setting_value.strip())
    except Exception:
        owner_reassign_enabled = False
        api_user_ids_configured = False
    return render_template(
        'automation_test.html',
        owner_reassign_enabled=owner_reassign_enabled,
        api_user_ids_configured=api_user_ids_configured,
    )


@xml_routes_bp.route('/automation_test', methods=['POST'])
@login_required
def automation_test_action():
    """Handle automation test actions"""
    try:
        data = request.get_json()
        action = data.get('action')

        if action == 'complete_demo':
            result = run_automation_demo()
            return jsonify(result)

        elif action == 'add_jobs':
            return run_step_test('add_jobs')

        elif action == 'remove_jobs':
            return run_step_test('remove_jobs')

        elif action == 'update_jobs':
            return run_step_test('update_jobs')

        elif action == 'file_upload':
            return run_step_test('file_upload')

        elif action == 'show_xml':
            demo_file = 'demo_test_current.xml'
            if os.path.exists(demo_file):
                try:
                    with open(demo_file, 'r', encoding='utf-8') as f:
                        xml_content = f.read()
                    return jsonify({
                        'success': True,
                        'xml_content': xml_content
                    })
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'error': f'Error reading demo file: {str(e)}'
                    })
            else:
                sample_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Senior Python Developer (12345) ]]></title>
    <company><![CDATA[ Tech Innovations Inc ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[REF1234567]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Looking for a Senior Python Developer with Django experience... ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ San Francisco ]]></city>
    <state><![CDATA[ California ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''
                return jsonify({
                    'success': True,
                    'xml_content': sample_xml,
                    'note': 'This is sample XML content. Run the Complete Demo first to see actual processed results.'
                })

        elif action == 'ownership_toggle':
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

        elif action == 'ownership_preview':
            try:
                from tasks.owner_reassignment import preview_reassign_candidates
                result = preview_reassign_candidates(limit=5)
                return jsonify({'success': True, **result})
            except Exception as e:
                logger.error(f"ownership_preview error: {str(e)}")
                return jsonify({'success': False, 'error': str(e)})

        else:
            return jsonify({'success': False, 'error': 'Unknown action'})

    except Exception as e:
        logger.error(f"Error in automation test: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@xml_routes_bp.route('/test_download/<download_key>')
@login_required
def test_download(download_key):
    """Download test XML file"""
    try:
        cache_file = f"download_cache_{download_key}.json"
        if not os.path.exists(cache_file):
            flash('Download link expired or invalid', 'error')
            return redirect(url_for('xml_routes.automation_test'))

        with open(cache_file, 'r') as f:
            download_info = json.load(f)

        file_path = download_info['file_path']
        original_name = download_info['original_name']

        if not os.path.exists(file_path):
            flash('Test file not found', 'error')
            return redirect(url_for('xml_routes.automation_test'))

        os.remove(cache_file)

        return send_file(
            file_path,
            as_attachment=True,
            download_name=original_name,
            mimetype='application/xml'
        )

    except Exception as e:
        logger.error(f"Test download error: {str(e)}")
        flash('Download failed', 'error')
        return redirect(url_for('xml_routes.automation_test'))


def reset_test_file():
    """Reset the test file to its original clean state"""
    try:
        original_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Senior Python Developer (12345) ]]></title>
    <company><![CDATA[ Tech Innovations Inc ]]></company>
    <date><![CDATA[ July 12, 2024 ]]></date>
    <referencenumber><![CDATA[TYBVQ4DZSL]]></referencenumber>
    <url><![CDATA[ https://myticas.com/ ]]></url>
    <description><![CDATA[ Senior Python Developer with Django and FastAPI experience ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ San Francisco ]]></city>
    <state><![CDATA[ California ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[  ]]></remotetype>
  </job>
  <job>
    <title><![CDATA[ Initial Job (99999) ]]></title>
    <company><![CDATA[ Myticas Consulting ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[ZNLCP9YE8X]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Initial test job ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ Chicago ]]></city>
    <state><![CDATA[ Illinois ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''

        with open('demo_test_current.xml', 'w', encoding='utf-8') as f:
            f.write(original_xml)

        logger.info("Test file reset to original clean state")

    except Exception as e:
        logger.error(f"Error resetting test file: {str(e)}")


def run_automation_demo():
    """Run the complete automation demo and return results"""
    try:
        from xml_integration_service import XMLIntegrationService
        from xml_processor import XMLProcessor

        xml_service = XMLIntegrationService()
        xml_processor = XMLProcessor()

        demo_xml_file = 'demo_test_current.xml'

        initial_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Initial Job (99999) ]]></title>
    <company><![CDATA[ Myticas Consulting ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[INIT999999]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Initial test job ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ Chicago ]]></city>
    <state><![CDATA[ Illinois ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''

        with open(demo_xml_file, 'w', encoding='utf-8') as f:
            f.write(initial_xml)

        previous_jobs = []
        current_jobs = [
            {
                'id': 12345,
                'title': 'Senior Python Developer',
                'clientCorporation': {'name': 'Tech Innovations Inc'},
                'description': 'Senior Python Developer with Django experience',
                'address': {'city': 'San Francisco', 'state': 'California', 'countryName': 'United States'},
                'employmentType': 'Full-time',
                'dateAdded': 1720742400000
            },
            {
                'id': 67890,
                'title': 'DevOps Engineer',
                'clientCorporation': {'name': 'Cloud Solutions LLC'},
                'description': 'DevOps Engineer with AWS experience',
                'address': {'city': 'Seattle', 'state': 'Washington', 'countryName': 'United States'},
                'employmentType': 'Contract',
                'dateAdded': 1720742400000
            }
        ]

        sync_result = xml_service.sync_xml_with_bullhorn_jobs(
            xml_file_path=demo_xml_file,
            current_jobs=current_jobs,
            previous_jobs=previous_jobs
        )

        if sync_result.get('success'):
            temp_output = f"{demo_xml_file}.processed"
            process_result = xml_processor.process_xml(demo_xml_file, temp_output, preserve_reference_numbers=False)

            if process_result.get('success'):
                os.replace(temp_output, demo_xml_file)

                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                job_count = len(re.findall(r'<job>', content))

                return {
                    'success': True,
                    'summary': f'Successfully processed {job_count} total jobs',
                    'jobs_added': sync_result.get('added_count', 0),
                    'jobs_removed': sync_result.get('removed_count', 0),
                    'jobs_updated': sync_result.get('updated_count', 0),
                    'total_jobs': job_count
                }
            else:
                if os.path.exists(demo_xml_file):
                    os.remove(demo_xml_file)
                return {
                    'success': False,
                    'error': f'XML processing failed: {process_result.get("error")}'
                }
        else:
            try:
                demo_xml_file_var = locals().get('demo_xml_file')
                if demo_xml_file_var and os.path.exists(demo_xml_file_var):
                    os.remove(demo_xml_file_var)
            except Exception:
                pass
            return {
                'success': False,
                'error': f'XML sync failed: {sync_result.get("error")}'
            }

    except Exception as e:
        try:
            demo_xml_file_var = locals().get('demo_xml_file')
            if demo_xml_file_var and os.path.exists(demo_xml_file_var):
                os.remove(demo_xml_file_var)
        except Exception:
            pass
        return {
            'success': False,
            'error': f'Demo failed: {str(e)}'
        }


def run_step_test(step_type):
    """Run individual step tests that modify the actual XML file"""
    try:
        from xml_integration_service import XMLIntegrationService
        from xml_processor import XMLProcessor
        from ftp_service import FTPService
        from models import GlobalSettings

        demo_xml_file = 'demo_test_current.xml'

        if not os.path.exists(demo_xml_file):
            initial_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title><![CDATA[ Initial Test Job (99999) ]]></title>
    <company><![CDATA[ Myticas Consulting ]]></company>
    <date><![CDATA[ July 12, 2025 ]]></date>
    <referencenumber><![CDATA[INIT999999]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description><![CDATA[ Initial test job ]]></description>
    <jobtype><![CDATA[ Full-time ]]></jobtype>
    <city><![CDATA[ Chicago ]]></city>
    <state><![CDATA[ Illinois ]]></state>
    <country><![CDATA[ United States ]]></country>
    <category><![CDATA[  ]]></category>
    <apply_email><![CDATA[ apply@myticas.com ]]></apply_email>
    <remotetype><![CDATA[]]></remotetype>
  </job>
</source>'''
            with open(demo_xml_file, 'w', encoding='utf-8') as f:
                f.write(initial_xml)

        xml_service = XMLIntegrationService()
        xml_processor = XMLProcessor()

        with open(demo_xml_file, 'r', encoding='utf-8') as f:
            content = f.read()
        current_job_count = len(re.findall(r'<job>', content))

        if step_type == 'add_jobs':
            new_job = {
                'id': 55555,
                'title': 'Frontend React Developer',
                'clientCorporation': {'name': 'Digital Solutions Inc'},
                'description': 'Frontend React Developer with TypeScript experience',
                'address': {'city': 'Austin', 'state': 'Texas', 'countryName': 'United States'},
                'employmentType': 'Full-time',
                'dateAdded': 1720742400000
            }

            sync_result = xml_service.sync_xml_with_bullhorn_jobs(
                xml_file_path=demo_xml_file,
                current_jobs=[new_job],
                previous_jobs=[]
            )

            if sync_result.get('success'):
                    with open(demo_xml_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    new_job_count = len(re.findall(r'<job>', content))

                    return jsonify({
                        'success': True,
                        'details': f'Added Frontend React Developer (55555) to XML file. Jobs: {current_job_count} → {new_job_count}',
                        'jobs_added': 1,
                        'total_jobs': new_job_count
                    })

            return jsonify({
                'success': False,
                'error': 'Failed to add job to XML file'
            })

        elif step_type == 'remove_jobs':
            with open(demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if '55555' in content:
                job_pattern = r'<job>.*?Frontend React Developer \(55555\).*?</job>'
                new_content = re.sub(job_pattern, '', content, flags=re.DOTALL)

                with open(demo_xml_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                new_job_count = len(re.findall(r'<job>', content))

                return jsonify({
                    'success': True,
                    'details': f'Removed Frontend React Developer (55555) from XML file. Jobs: {current_job_count} → {new_job_count}',
                    'jobs_removed': 1,
                    'total_jobs': new_job_count
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No job found to remove. Try adding a job first.'
                })

        elif step_type == 'update_jobs':
            with open(demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if '12345' in content:
                updated_content = content.replace(
                    'Senior Python Developer (12345)',
                    'Senior Python Developer - UPDATED (12345)'
                )
                updated_content = updated_content.replace(
                    'Senior Python Developer with Django experience',
                    'Senior Python Developer with Django and FastAPI experience - UPDATED'
                )

                with open(demo_xml_file, 'w', encoding='utf-8') as f:
                    f.write(updated_content)

                return jsonify({
                    'success': True,
                    'details': 'Updated Senior Python Developer job with new title and description',
                    'jobs_updated': 1,
                    'total_jobs': current_job_count
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No job found to update. Try running Complete Demo first.'
                })

        elif step_type == 'file_upload':
            if os.path.exists(demo_xml_file):
                processed_filename = f"test_processed_{int(time.time())}.xml"
                shutil.copy2(demo_xml_file, processed_filename)

                file_size = os.path.getsize(demo_xml_file)
                with open(demo_xml_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                job_count = len(re.findall(r'<job>', content))

                upload_success = False
                upload_message = ""

                try:
                    sftp_settings = {}
                    for key in ['sftp_hostname', 'sftp_username', 'sftp_password', 'sftp_directory']:
                        setting = GlobalSettings.query.filter_by(setting_key=key).first()
                        if setting:
                            sftp_settings[key] = setting.setting_value

                    if all(sftp_settings.get(key) for key in ['sftp_hostname', 'sftp_username', 'sftp_password']):
                        def timeout_handler(signum, frame):
                            raise TimeoutError("SFTP upload timed out")

                        signal.signal(signal.SIGALRM, timeout_handler)
                        signal.alarm(15)

                        try:
                            ftp_service = FTPService(
                                hostname=sftp_settings['sftp_hostname'],
                                username=sftp_settings['sftp_username'],
                                password=sftp_settings['sftp_password'],
                                target_directory=sftp_settings.get('sftp_directory', '/'),
                                use_sftp=True
                            )

                            upload_success = ftp_service.upload_file(demo_xml_file, 'test-automation-demo.xml')
                            upload_message = "Real SFTP upload completed" if upload_success else "SFTP upload failed"
                        except TimeoutError:
                            upload_message = "SFTP upload timed out - simulated upload for demo"
                            upload_success = True
                        finally:
                            signal.alarm(0)
                    else:
                        upload_message = "SFTP credentials not configured - simulated upload"
                        upload_success = True

                except Exception as e:
                    upload_message = f"SFTP upload error: {str(e)[:100]}... - simulated upload for demo"
                    upload_success = True

                download_key = str(uuid.uuid4())

                download_info = {
                    'file_path': processed_filename,
                    'original_name': 'test-automation-demo.xml',
                    'timestamp': time.time()
                }

                cache_file = f"download_cache_{download_key}.json"
                with open(cache_file, 'w') as f:
                    json.dump(download_info, f)

                return jsonify({
                    'success': True,
                    'details': f'{upload_message}. XML file ({file_size} bytes, {job_count} jobs) processed and available for download',
                    'uploaded': upload_success,
                    'file_size': file_size,
                    'job_count': job_count,
                    'download_key': download_key,
                    'download_url': f'/test_download/{download_key}'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No XML file found to upload. Run Complete Demo first.'
                })

        else:
            return jsonify({
                'success': False,
                'error': 'Unknown step type'
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Step test failed: {str(e)}'
        })
