import json
from timezone_utils import jinja_eastern_time, jinja_eastern_short, jinja_eastern_datetime


def format_activity_details(activity_type, details_json):
    """
    Convert JSON activity details into user-friendly formatted text.
    
    Args:
        activity_type: String indicating the type of activity
        details_json: JSON string containing activity details
    
    Returns:
        Formatted string suitable for display
    """
    if not details_json:
        return "No details available"
    
    try:
        details = json.loads(details_json) if isinstance(details_json, str) else details_json
    except (json.JSONDecodeError, TypeError):
        return details_json if isinstance(details_json, str) else "No details available"
    
    if activity_type == 'email_notification':
        monitor_type = details.get('monitor_type', 'Unknown')
        changes = details.get('changes_detected', 0)
        added = details.get('added_jobs', 0)
        removed = details.get('removed_jobs', 0)
        modified = details.get('modified_jobs', 0)
        
        if changes == 0:
            return f"{monitor_type}: No changes detected"
        
        change_parts = []
        if added > 0:
            change_parts.append(f"+{added} added")
        if removed > 0:
            change_parts.append(f"-{removed} removed")
        if modified > 0:
            change_parts.append(f"~{modified} modified")
        
        change_summary = ", ".join(change_parts) if change_parts else "processed"
        return f"{monitor_type}: {changes} jobs ({change_summary})"
    
    elif activity_type == 'xml_sync_completed':
        jobs_processed = details.get('jobs_processed', details.get('total_jobs', 0))
        execution_time = details.get('execution_time', '')
        cycle_time = details.get('cycle_time', 0)
        
        time_info = f" in {cycle_time:.1f}s" if cycle_time > 0 else ""
        return f"XML Sync: {jobs_processed} jobs processed{time_info}"
    
    elif activity_type == 'check_completed':
        results = details.get('results', {})
        monitor_name = details.get('monitor_name', 'Monitor')
        
        if isinstance(results, dict):
            jobs_found = results.get('jobs_found', results.get('total_jobs', 0))
            return f"{monitor_name}: {jobs_found} jobs verified"
        else:
            return f"{monitor_name}: Check completed"
    
    elif activity_type == 'job_added':
        job_title = details.get('job_title', details.get('title', 'Unknown Job'))
        company = details.get('company', '')
        location = details.get('location', '')
        
        parts = [job_title]
        if company:
            parts.append(f"at {company}")
        if location:
            parts.append(f"in {location}")
        
        return " ".join(parts)
    
    elif activity_type == 'job_removed':
        job_title = details.get('job_title', details.get('title', 'Unknown Job'))
        reason = details.get('reason', 'No longer active')
        return f"{job_title} - {reason}"
    
    elif activity_type == 'job_modified':
        changes = details.get('changes', {})
        if isinstance(changes, dict):
            change_list = []
            for field, change_info in changes.items():
                if isinstance(change_info, dict) and 'old' in change_info and 'new' in change_info:
                    change_list.append(f"{field} updated")
                else:
                    change_list.append(field)
            
            if change_list:
                return f"Updated: {', '.join(change_list)}"
        
        return details.get('description', 'Job details updated')
    
    elif activity_type == 'error':
        error_msg = details.get('error', details.get('message', 'Unknown error'))
        return f"Error: {error_msg}"
    
    elif activity_type == 'automated_upload':
        jobs_count = details.get('jobs_count', details.get('total_jobs', 0))
        success = details.get('upload_success', False)
        
        status = "successful" if success else "failed"
        return f"Automated Upload: {jobs_count} jobs - {status}"
    
    elif activity_type == 'scheduled_processing':
        if isinstance(details, str):
            return details
        jobs_processed = details.get('jobs_processed', 0)
        schedule_name = details.get('schedule_name', 'Unknown Schedule')
        return f"Scheduled Processing: {schedule_name} - {jobs_processed} jobs processed"
    
    elif activity_type == 'scheduled_processing_error':
        if isinstance(details, str):
            return details
        error_msg = details.get('error', 'Unknown error')
        schedule_name = details.get('schedule_name', 'Unknown Schedule')
        return f"Processing Error: {schedule_name} - {error_msg}"
    
    elif activity_type == 'monitoring_cycle_completed':
        if isinstance(details, str):
            return details
        added = details.get('jobs_added', 0)
        removed = details.get('jobs_removed', 0) 
        updated = details.get('jobs_updated', 0)
        excluded = details.get('excluded_jobs', 0)
        total = details.get('total_jobs', 0)
        
        changes = []
        if added > 0:
            changes.append(f"+{added} added")
        if removed > 0:
            changes.append(f"-{removed} removed")  
        if updated > 0:
            changes.append(f"~{updated} updated")
        if excluded > 0:
            changes.append(f"🚫{excluded} excluded")
            
        if changes:
            change_summary = ", ".join(changes)
            return f"Monitoring Cycle: {total} total jobs ({change_summary})"
        else:
            return f"Monitoring Cycle: {total} total jobs (no changes)"
    
    if isinstance(details, dict):
        if 'message' in details:
            return details['message']
        elif 'description' in details:
            return details['description']
        elif 'total_jobs' in details:
            return f"Processed {details['total_jobs']} jobs"
        elif 'jobs_count' in details:
            return f"Processed {details['jobs_count']} jobs"
    
    detail_str = str(details) if not isinstance(details, str) else details
    return detail_str[:100] + "..." if len(detail_str) > 100 else detail_str


def register_filters(app):
    @app.template_filter('format_activity')
    def format_activity_filter(activity):
        """Template filter to format activity details for display"""
        return format_activity_details(activity.activity_type, activity.details)

    @app.template_filter('eastern_time')
    def eastern_time_filter(utc_dt, format_string='%b %d, %Y at %I:%M %p %Z'):
        """Convert UTC datetime to Eastern Time with custom format"""
        return jinja_eastern_time(utc_dt, format_string)

    @app.template_filter('eastern_short')
    def eastern_short_filter(utc_dt):
        """Convert UTC datetime to short Eastern Time format (Oct 19, 2025 9:44 PM EDT)"""
        return jinja_eastern_short(utc_dt)

    @app.template_filter('eastern_datetime')
    def eastern_datetime_filter(utc_dt):
        """Convert UTC datetime to datetime Eastern Time format (2025-10-19 09:44 PM EDT)"""
        return jinja_eastern_datetime(utc_dt)
