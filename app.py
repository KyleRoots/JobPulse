import os
import logging
from flask import Flask, render_template, request, send_file, flash, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import tempfile
import uuid
from xml_processor import XMLProcessor
from email_service import EmailService
from ftp_service import FTPService
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
import atexit
import shutil

# Configure logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

# Create database instance
db = SQLAlchemy(model_class=Base)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-12345")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Initialize database
db.init_app(app)


# Configuration
UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'xml'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file size

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Import and initialize models
from models import create_models
ScheduleConfig, ProcessingLog = create_models(db)

# Initialize database tables
with app.app_context():
    db.create_all()

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Cleanup scheduler on exit
atexit.register(lambda: scheduler.shutdown())

def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_scheduled_files():
    """Process all scheduled files that are due for processing"""
    with app.app_context():
        try:
            # Get all active schedules that are due
            now = datetime.utcnow()
            due_schedules = ScheduleConfig.query.filter(
                ScheduleConfig.is_active == True,
                ScheduleConfig.next_run <= now
            ).all()
            
            for schedule in due_schedules:
                try:
                    # Check if file exists
                    if not os.path.exists(schedule.file_path):
                        app.logger.warning(f"Scheduled file not found: {schedule.file_path}")
                        continue
                    
                    # Process the file
                    processor = XMLProcessor()
                    
                    # Create backup of original file
                    backup_path = f"{schedule.file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    shutil.copy2(schedule.file_path, backup_path)
                    
                    # Generate temporary output filename
                    temp_output = f"{schedule.file_path}.temp"
                    
                    # Process the XML
                    result = processor.process_xml(schedule.file_path, temp_output)
                    
                    # Log the processing result
                    log_entry = ProcessingLog(
                        schedule_config_id=schedule.id,
                        file_path=schedule.file_path,
                        processing_type='scheduled',
                        jobs_processed=result.get('jobs_processed', 0),
                        success=result.get('success', False),
                        error_message=result.get('error') if not result.get('success') else None
                    )
                    db.session.add(log_entry)
                    
                    # Update schedule
                    schedule.last_run = now
                    schedule.calculate_next_run()
                    
                    if result.get('success'):
                        # Replace original file with updated version
                        os.replace(temp_output, schedule.file_path)
                        app.logger.info(f"Successfully processed scheduled file: {schedule.file_path}")
                        
                        # Get original filename for email/FTP
                        original_filename = os.path.basename(schedule.file_path)
                        
                        # Send email notification if configured
                        if schedule.send_email_notifications and schedule.notification_email:
                            try:
                                email_service = EmailService()
                                email_sent = email_service.send_processing_notification(
                                    to_email=schedule.notification_email,
                                    schedule_name=schedule.name,
                                    jobs_processed=result.get('jobs_processed', 0),
                                    xml_file_path=schedule.file_path,
                                    original_filename=original_filename
                                )
                                if email_sent:
                                    app.logger.info(f"Email notification sent to {schedule.notification_email}")
                                else:
                                    app.logger.warning(f"Failed to send email notification to {schedule.notification_email}")
                            except Exception as e:
                                app.logger.error(f"Error sending email notification: {str(e)}")
                        
                        # Upload to FTP if configured
                        if schedule.auto_upload_ftp and schedule.ftp_hostname and schedule.ftp_username and schedule.ftp_password:
                            try:
                                ftp_service = FTPService(
                                    hostname=schedule.ftp_hostname,
                                    username=schedule.ftp_username,
                                    password=schedule.ftp_password,
                                    target_directory=schedule.ftp_directory or "/"
                                )
                                ftp_uploaded = ftp_service.upload_file(
                                    local_file_path=schedule.file_path,
                                    remote_filename=original_filename
                                )
                                if ftp_uploaded:
                                    app.logger.info(f"File uploaded to FTP server: {original_filename}")
                                else:
                                    app.logger.warning(f"Failed to upload file to FTP server")
                            except Exception as e:
                                app.logger.error(f"Error uploading to FTP: {str(e)}")
                        
                    else:
                        # Clean up temp file on failure
                        if os.path.exists(temp_output):
                            os.remove(temp_output)
                        app.logger.error(f"Failed to process scheduled file: {schedule.file_path} - {result.get('error')}")
                        
                        # Send error notification email if configured
                        if schedule.send_email_notifications and schedule.notification_email:
                            try:
                                email_service = EmailService()
                                email_service.send_processing_error_notification(
                                    to_email=schedule.notification_email,
                                    schedule_name=schedule.name,
                                    error_message=result.get('error', 'Unknown error')
                                )
                            except Exception as e:
                                app.logger.error(f"Error sending error notification email: {str(e)}")
                    
                except Exception as e:
                    app.logger.error(f"Error processing scheduled file {schedule.file_path}: {str(e)}")
                    
                    # Log the error
                    log_entry = ProcessingLog(
                        schedule_config_id=schedule.id,
                        file_path=schedule.file_path,
                        processing_type='scheduled',
                        jobs_processed=0,
                        success=False,
                        error_message=str(e)
                    )
                    db.session.add(log_entry)
            
            db.session.commit()
            
        except Exception as e:
            app.logger.error(f"Error in scheduled processing: {str(e)}")
            db.session.rollback()

# Add the scheduled job to check every 5 minutes
scheduler.add_job(
    func=process_scheduled_files,
    trigger=IntervalTrigger(minutes=5),
    id='process_scheduled_files',
    name='Process Scheduled XML Files',
    replace_existing=True
)

@app.route('/')
def index():
    """Main page with file upload form"""
    return render_template('index.html')

@app.route('/scheduler')
def scheduler_dashboard():
    """Scheduling dashboard for automated processing"""
    # Get all active schedules
    schedules = ScheduleConfig.query.filter_by(is_active=True).all()
    
    # Get recent processing logs
    recent_logs = ProcessingLog.query.order_by(ProcessingLog.processed_at.desc()).limit(10).all()
    
    return render_template('scheduler.html', schedules=schedules, recent_logs=recent_logs)

@app.route('/api/schedules', methods=['POST'])
def create_schedule():
    """Create a new automated processing schedule"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'file_path', 'schedule_days']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Validate file exists
        if not os.path.exists(data['file_path']):
            return jsonify({'success': False, 'error': 'File does not exist'}), 400
        
        # Validate XML file
        processor = XMLProcessor()
        if not processor.validate_xml(data['file_path']):
            return jsonify({'success': False, 'error': 'Invalid XML file'}), 400
        
        # Create new schedule
        schedule = ScheduleConfig(
            name=data['name'],
            file_path=data['file_path'],
            schedule_days=int(data['schedule_days']),
            # Email notification settings
            send_email_notifications=data.get('send_email_notifications', False),
            notification_email=data.get('notification_email') if data.get('send_email_notifications') else None,
            # FTP upload settings
            auto_upload_ftp=data.get('auto_upload_ftp', False),
            ftp_hostname=data.get('ftp_hostname') if data.get('auto_upload_ftp') else None,
            ftp_username=data.get('ftp_username') if data.get('auto_upload_ftp') else None,
            ftp_password=data.get('ftp_password') if data.get('auto_upload_ftp') else None,
            ftp_directory=data.get('ftp_directory', '/') if data.get('auto_upload_ftp') else None
        )
        schedule.calculate_next_run()
        
        db.session.add(schedule)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Schedule created successfully',
            'schedule_id': schedule.id
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error creating schedule: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    """Delete a schedule"""
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        schedule.is_active = False
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Schedule deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedules/<int:schedule_id>/run', methods=['POST'])
def run_schedule_now(schedule_id):
    """Manually trigger a schedule to run now"""
    try:
        schedule = ScheduleConfig.query.get_or_404(schedule_id)
        
        # Check if file exists
        if not os.path.exists(schedule.file_path):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        # Process the file
        processor = XMLProcessor()
        
        # Create backup
        backup_path = f"{schedule.file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(schedule.file_path, backup_path)
        
        # Generate temporary output
        temp_output = f"{schedule.file_path}.temp"
        
        # Process the XML
        result = processor.process_xml(schedule.file_path, temp_output)
        
        # Log the processing result
        log_entry = ProcessingLog(
            schedule_config_id=schedule.id,
            file_path=schedule.file_path,
            processing_type='manual',
            jobs_processed=result.get('jobs_processed', 0),
            success=result.get('success', False),
            error_message=result.get('error') if not result.get('success') else None
        )
        db.session.add(log_entry)
        
        if result.get('success'):
            # Replace original file with updated version
            os.replace(temp_output, schedule.file_path)
            
            # Update last run time but don't change next scheduled run
            schedule.last_run = datetime.utcnow()
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'Successfully processed {result.get("jobs_processed", 0)} jobs',
                'jobs_processed': result.get('jobs_processed', 0)
            })
        else:
            # Clean up temp file on failure
            if os.path.exists(temp_output):
                os.remove(temp_output)
            db.session.commit()
            return jsonify({'success': False, 'error': result.get('error')}), 500
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error running schedule manually: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/upload-schedule-file', methods=['POST'])
def upload_schedule_file():
    """Handle file upload for scheduling"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        # Create uploads directory if it doesn't exist
        uploads_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'scheduled_files')
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Save file with secure filename
        filename = secure_filename(file.filename or 'unknown.xml')
        unique_id = str(uuid.uuid4())[:8]
        final_filename = f"{unique_id}_{filename}"
        file_path = os.path.join(uploads_dir, final_filename)
        
        file.save(file_path)
        
        # Validate XML
        processor = XMLProcessor()
        if not processor.validate_xml(file_path):
            os.remove(file_path)
            return jsonify({'success': False, 'error': 'Invalid XML file'}), 400
        
        job_count = processor.count_jobs(file_path)
        
        return jsonify({
            'success': True,
            'file_path': file_path,
            'filename': filename,
            'job_count': job_count
        })
        
    except Exception as e:
        app.logger.error(f"Error uploading schedule file: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and processing"""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('index'))
        
        file = request.files['file']
        
        # Check if file was actually selected
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('index'))
        
        # Check file extension
        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload an XML file.', 'error')
            return redirect(url_for('index'))
        
        # Generate unique filename
        original_filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())[:8]
        input_filename = f"{unique_id}_{original_filename}"
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
        
        # Save uploaded file
        file.save(input_filepath)
        
        # Process the XML file
        processor = XMLProcessor()
        
        # Validate XML structure
        if not processor.validate_xml(input_filepath):
            flash('Invalid XML file structure. Please check your file and try again.', 'error')
            os.remove(input_filepath)
            return redirect(url_for('index'))
        
        # Generate output filename
        output_filename = f"updated_{original_filename}"
        output_filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_id}_{output_filename}")
        
        # Process the file
        result = processor.process_xml(input_filepath, output_filepath)
        
        # Clean up input file
        os.remove(input_filepath)
        
        if result['success']:
            flash(f'Successfully processed {result["jobs_processed"]} jobs with unique reference numbers', 'success')
            
            # Store output file info in session for download
            session_key = f"processed_file_{unique_id}"
            app.config[session_key] = {
                'filepath': output_filepath,
                'filename': output_filename,
                'jobs_processed': result['jobs_processed']
            }
            
            return render_template('index.html', 
                                 download_key=unique_id,
                                 filename=output_filename,
                                 jobs_processed=result['jobs_processed'])
        else:
            flash(f'Error processing file: {result["error"]}', 'error')
            return redirect(url_for('index'))
            
    except Exception as e:
        app.logger.error(f"Error in upload_file: {str(e)}")
        flash(f'An error occurred while processing the file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/download/<download_key>')
def download_file(download_key):
    """Download the processed file"""
    try:
        session_key = f"processed_file_{download_key}"
        
        if session_key not in app.config:
            flash('Download link has expired or is invalid', 'error')
            return redirect(url_for('index'))
        
        file_info = app.config[session_key]
        filepath = file_info['filepath']
        filename = file_info['filename']
        
        if not os.path.exists(filepath):
            flash('File not found', 'error')
            return redirect(url_for('index'))
        
        # Send file and clean up
        def remove_file(response):
            try:
                os.remove(filepath)
                del app.config[session_key]
            except Exception as e:
                app.logger.error(f"Error cleaning up file: {str(e)}")
            return response
        
        return send_file(filepath, 
                        as_attachment=True, 
                        download_name=filename,
                        mimetype='application/xml')
        
    except Exception as e:
        app.logger.error(f"Error in download_file: {str(e)}")
        flash(f'Error downloading file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/validate', methods=['POST'])
def validate_file():
    """Validate XML file structure without processing"""
    try:
        if 'file' not in request.files:
            return jsonify({'valid': False, 'error': 'No file uploaded'})
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'valid': False, 'error': 'No file selected'})
        
        if not allowed_file(file.filename):
            return jsonify({'valid': False, 'error': 'Invalid file type'})
        
        # Save temporary file for validation
        temp_filename = f"temp_{str(uuid.uuid4())[:8]}_{secure_filename(file.filename)}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
        file.save(temp_filepath)
        
        # Validate XML
        processor = XMLProcessor()
        is_valid = processor.validate_xml(temp_filepath)
        
        # Get job count if valid
        job_count = 0
        if is_valid:
            job_count = processor.count_jobs(temp_filepath)
        
        # Clean up
        os.remove(temp_filepath)
        
        return jsonify({
            'valid': is_valid,
            'job_count': job_count,
            'error': None if is_valid else 'Invalid XML structure'
        })
        
    except Exception as e:
        app.logger.error(f"Error in validate_file: {str(e)}")
        return jsonify({'valid': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
