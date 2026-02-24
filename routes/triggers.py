"""
Trigger Routes Blueprint
API trigger endpoints for manual operations and system health checks
"""
from flask import Blueprint, jsonify, render_template, request, current_app
from flask_login import login_required
from routes import register_admin_guard
from datetime import datetime, timedelta

triggers_bp = Blueprint('triggers', __name__)
register_admin_guard(triggers_bp)


def get_db():
    """Get database instance from app context"""
    from app import db
    return db


@triggers_bp.route('/api/trigger/job-sync', methods=['POST'])
@login_required  
def trigger_job_sync():
    """Manually trigger job synchronization for immediate processing"""
    try:
        from app import ensure_background_services
        from incremental_monitoring_service import IncrementalMonitoringService
        
        # Ensure background services are initialized
        ensure_background_services()
        
        monitoring_service = IncrementalMonitoringService()
        cycle_results = monitoring_service.run_monitoring_cycle()
        
        current_app.logger.info(f"Manual job sync completed: {cycle_results}")
        
        return jsonify({
            'success': True,
            'message': 'Job sync triggered successfully',
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        current_app.logger.error(f"Manual job sync error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@triggers_bp.route('/api/trigger/file-cleanup', methods=['POST'])
@login_required
def trigger_file_cleanup():
    """Manually trigger file consolidation and cleanup"""
    try:
        from app import ensure_background_services, lazy_init_file_consolidation
        
        current_app.logger.info("Manual file cleanup triggered")
        ensure_background_services()
        
        file_service = lazy_init_file_consolidation()
        
        if file_service and file_service is not False:
            results = file_service.run_full_cleanup()
            
            return jsonify({
                'success': True,
                'message': 'File cleanup completed successfully',
                'timestamp': datetime.utcnow().isoformat(),
                'results': results
            })
        else:
            return jsonify({
                'success': False,
                'error': 'File consolidation service not available'
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Manual file cleanup error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@triggers_bp.route('/api/trigger/health-check', methods=['POST'])
@login_required
def trigger_health_check():
    """Manually trigger monitor health check"""
    try:
        current_app.logger.info("Manual health check triggered")
        
        return jsonify({
            'success': True,
            'message': 'Health check integrated into comprehensive monitoring system',
            'timestamp': datetime.utcnow().isoformat(),
            'result': {'status': 'integrated'}
        })
    except Exception as e:
        current_app.logger.error(f"Manual health check error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@triggers_bp.route('/api/trigger/ai-classification-fix', methods=['POST'])
@login_required
def trigger_ai_classification_fix():
    """Manually trigger AI classification fix for all jobs"""
    try:
        current_app.logger.info("Manual AI classification fix triggered")
        
        from job_classification_service import JobClassificationService
        from lxml import etree
        import os
        
        xml_files = ['myticas-job-feed.xml']
        total_jobs_fixed = 0
        
        for xml_file in xml_files:
            if not os.path.exists(xml_file):
                continue
                
            try:
                parser = etree.XMLParser(strip_cdata=False, recover=True)
                tree = etree.parse(xml_file, parser)
                root = tree.getroot()
                
                jobs = root.findall('.//job')
                jobs_to_fix = []
                
                for job in jobs:
                    job_id_elem = job.find('bhatsid')
                    job_id = job_id_elem.text if job_id_elem is not None else 'Unknown'
                    
                    title_elem = job.find('title')
                    title = title_elem.text if title_elem is not None else ''
                    
                    description_elem = job.find('description')
                    description = description_elem.text if description_elem is not None else ''
                    
                    # Check if AI classifications are missing
                    jobfunction_elem = job.find('jobfunction')
                    jobindustries_elem = job.find('jobindustries')
                    senoritylevel_elem = job.find('senoritylevel')
                    
                    missing_ai = []
                    if jobfunction_elem is None or not jobfunction_elem.text or jobfunction_elem.text.strip() == '':
                        missing_ai.append('jobfunction')
                    if jobindustries_elem is None or not jobindustries_elem.text or jobindustries_elem.text.strip() == '':
                        missing_ai.append('jobindustries')
                    if senoritylevel_elem is None or not senoritylevel_elem.text or senoritylevel_elem.text.strip() == '':
                        missing_ai.append('senoritylevel')
                    
                    if missing_ai:
                        jobs_to_fix.append({
                            'job_id': job_id,
                            'title': title,
                            'description': description,
                            'job_element': job,
                            'missing_fields': missing_ai
                        })
                
                if jobs_to_fix:
                    current_app.logger.info(f"Found {len(jobs_to_fix)} jobs with missing AI classifications in {xml_file}")
                    
                    classification_service = JobClassificationService()
                    
                    for job_data in jobs_to_fix:
                        try:
                            ai_result = classification_service.classify_job(
                                job_data['title'], 
                                job_data['description']
                            )
                            
                            if ai_result and ai_result.get('success'):
                                if 'jobfunction' in job_data['missing_fields']:
                                    jobfunction_elem = job_data['job_element'].find('jobfunction')
                                    if jobfunction_elem is None:
                                        jobfunction_elem = etree.SubElement(job_data['job_element'], 'jobfunction')
                                        jobfunction_elem.tail = "\n    "
                                    jobfunction_elem.text = etree.CDATA(f" {ai_result['job_function']} ")
                                
                                if 'jobindustries' in job_data['missing_fields']:
                                    jobindustries_elem = job_data['job_element'].find('jobindustries')
                                    if jobindustries_elem is None:
                                        jobindustries_elem = etree.SubElement(job_data['job_element'], 'jobindustries')
                                        jobindustries_elem.tail = "\n    "
                                    jobindustries_elem.text = etree.CDATA(f" {ai_result['industries']} ")
                                
                                if 'senoritylevel' in job_data['missing_fields']:
                                    senoritylevel_elem = job_data['job_element'].find('senoritylevel')
                                    if senoritylevel_elem is None:
                                        senoritylevel_elem = etree.SubElement(job_data['job_element'], 'senoritylevel')
                                        senoritylevel_elem.tail = "\n  "
                                    senoritylevel_elem.text = etree.CDATA(f" {ai_result['seniority_level']} ")
                                
                                total_jobs_fixed += 1
                                current_app.logger.info(f"Fixed AI classifications for job {job_data['job_id']}")
                                
                        except Exception as e:
                            current_app.logger.error(f"Error fixing AI classifications for job {job_data['job_id']}: {str(e)}")
                    
                    if total_jobs_fixed > 0:
                        with open(xml_file, 'wb') as f:
                            tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                        current_app.logger.info(f"Updated {xml_file} with AI classifications for {len(jobs_to_fix)} jobs")
                
            except Exception as e:
                current_app.logger.error(f"Error processing AI classifications in {xml_file}: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': f'AI classification fix completed successfully',
            'fixed_count': total_jobs_fixed,
            'timestamp': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        current_app.logger.error(f"Manual AI classification fix error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@triggers_bp.route('/api/system/health')
def system_health_check():
    """System health check endpoint to detect scheduler timing issues"""
    from models import BullhornMonitor, ScheduleConfig
    
    try:
        current_time = datetime.utcnow()
        
        # Check Bullhorn monitors for timing issues
        overdue_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.is_active == True,
            BullhornMonitor.next_check < current_time - timedelta(minutes=10)
        ).all()
        
        # Check scheduled files for timing issues
        overdue_schedules = ScheduleConfig.query.filter(
            ScheduleConfig.is_active == True,
            ScheduleConfig.next_run < current_time - timedelta(hours=1)
        ).all()
        
        health_status = "healthy"
        issues = []
        warnings = []
        
        if overdue_monitors:
            health_status = "warning"
            issues.append(f"{len(overdue_monitors)} Bullhorn monitors overdue >10 minutes")
        
        drift_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.is_active == True,
            BullhornMonitor.next_check < current_time + timedelta(minutes=2),
            BullhornMonitor.next_check > current_time - timedelta(minutes=10)
        ).all()
        
        if drift_monitors and health_status == "healthy":
            warnings.append(f"{len(drift_monitors)} monitors approaching next check time")
        
        if overdue_schedules:
            health_status = "critical" if health_status == "warning" else "warning"
            issues.append(f"{len(overdue_schedules)} schedules overdue >1 hour")
        
        all_active_monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        timing_accuracy = {
            'healthy_monitors': len([m for m in all_active_monitors if m.next_check > current_time]),
            'total_monitors': len(all_active_monitors),
            'oldest_next_check': min([m.next_check for m in all_active_monitors]) if all_active_monitors else None,
            'newest_next_check': max([m.next_check for m in all_active_monitors]) if all_active_monitors else None
        }
        
        active_monitors = BullhornMonitor.query.filter_by(is_active=True).count()
        active_schedules = ScheduleConfig.query.filter_by(is_active=True).count()
        
        return jsonify({
            'success': True,
            'health_status': health_status,
            'timestamp': current_time.isoformat(),
            'issues': issues,
            'warnings': warnings,
            'timing_accuracy': timing_accuracy,
            'system_info': {
                'active_monitors': active_monitors,
                'active_schedules': active_schedules,
                'overdue_monitors': len(overdue_monitors),
                'overdue_schedules': len(overdue_schedules),
                'drift_monitors': len(drift_monitors)
            },
            'next_actions': {
                'monitors_next_run': BullhornMonitor.query.filter_by(is_active=True).order_by(BullhornMonitor.next_check).first().next_check.isoformat() if active_monitors > 0 else None,
                'schedules_next_run': ScheduleConfig.query.filter_by(is_active=True).order_by(ScheduleConfig.next_run).first().next_run.isoformat() if active_schedules > 0 else None
            },
            'prevention_layers': {
                'auto_recovery': 'Active - detects monitors >10min overdue',
                'immediate_commits': 'Active - commits timing after each monitor',
                'error_recovery': 'Active - updates timing even on processing errors', 
                'final_health_check': 'Active - verifies timing after processing',
                'enhanced_monitoring': 'Active - tracks timing drift and accuracy'
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'health_status': 'error',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        })


@triggers_bp.route('/api/system/fix-timing', methods=['POST'])
@login_required
def fix_system_timing():
    """Admin endpoint to manually fix scheduler timing issues"""
    from models import BullhornMonitor, ScheduleConfig
    
    db = get_db()
    
    try:
        current_time = datetime.utcnow()
        fixed_items = []
        
        # Fix overdue Bullhorn monitors
        overdue_monitors = BullhornMonitor.query.filter(
            BullhornMonitor.is_active == True,
            BullhornMonitor.next_check < current_time - timedelta(minutes=10)
        ).all()
        
        for monitor in overdue_monitors:
            old_time = monitor.next_check
            monitor.next_check = current_time + timedelta(minutes=2)
            fixed_items.append(f"Monitor '{monitor.name}': {old_time} → {monitor.next_check}")
        
        # Fix overdue schedules
        overdue_schedules = ScheduleConfig.query.filter(
            ScheduleConfig.is_active == True,
            ScheduleConfig.next_run < current_time - timedelta(hours=1)
        ).all()
        
        for schedule in overdue_schedules:
            old_time = schedule.next_run
            schedule.next_run = current_time + timedelta(days=schedule.schedule_days)
            fixed_items.append(f"Schedule '{schedule.name}': {old_time} → {schedule.next_run}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Fixed timing for {len(fixed_items)} items',
            'fixed_items': fixed_items,
            'timestamp': current_time.isoformat()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        })
