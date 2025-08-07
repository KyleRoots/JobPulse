"""
Rapid Change Tracker for Bullhorn Monitoring
Tracks and aggregates multiple job state changes within a single monitoring cycle
"""

from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
import json
from collections import defaultdict

class RapidChangeTracker:
    """
    Tracks rapid job state changes within monitoring cycles.
    Designed to capture scenarios where a job is added, removed, and modified
    all within a single 2-minute monitoring window.
    """
    
    def __init__(self):
        # Track all state transitions for each job
        # Structure: {job_id: [{'timestamp': datetime, 'action': 'added/removed/modified', 'details': {...}}]}
        self.job_state_timeline = defaultdict(list)
        
        # Track current cycle start time
        self.cycle_start_time = None
        
        # Track jobs that had multiple state changes
        self.rapid_change_jobs = set()
        
    def start_new_cycle(self):
        """Start tracking a new monitoring cycle"""
        self.job_state_timeline.clear()
        self.rapid_change_jobs.clear()
        self.cycle_start_time = datetime.utcnow()
        
    def record_job_added(self, job_id: int, job_data: dict):
        """Record a job addition"""
        self.job_state_timeline[job_id].append({
            'timestamp': datetime.utcnow(),
            'action': 'added',
            'title': job_data.get('title', ''),
            'owner': self._extract_owner(job_data),
            'details': job_data
        })
        self._check_rapid_change(job_id)
        
    def record_job_removed(self, job_id: int, job_data: dict):
        """Record a job removal"""
        self.job_state_timeline[job_id].append({
            'timestamp': datetime.utcnow(),
            'action': 'removed',
            'title': job_data.get('title', ''),
            'owner': self._extract_owner(job_data),
            'details': job_data
        })
        self._check_rapid_change(job_id)
        
    def record_job_modified(self, job_id: int, job_data: dict, changes: list):
        """Record a job modification with specific changes"""
        self.job_state_timeline[job_id].append({
            'timestamp': datetime.utcnow(),
            'action': 'modified',
            'title': job_data.get('title', ''),
            'owner': self._extract_owner(job_data),
            'changes': changes,
            'details': job_data
        })
        self._check_rapid_change(job_id)
        
    def _extract_owner(self, job_data: dict) -> str:
        """Extract owner/account manager from job data"""
        # Try userID field first
        if job_data.get('userID') and isinstance(job_data['userID'], dict):
            first_name = job_data['userID'].get('firstName', '').strip()
            last_name = job_data['userID'].get('lastName', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        # Try owner field
        if job_data.get('owner') and isinstance(job_data['owner'], dict):
            first_name = job_data['owner'].get('firstName', '').strip()
            last_name = job_data['owner'].get('lastName', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
                
        # Try assignedUsers
        if job_data.get('assignedUsers') and len(job_data['assignedUsers']) > 0:
            first_user = job_data['assignedUsers'][0]
            if isinstance(first_user, dict):
                first_name = first_user.get('firstName', '').strip()
                last_name = first_user.get('lastName', '').strip()
                if first_name or last_name:
                    return f"{first_name} {last_name}".strip()
                    
        return "Unknown"
        
    def _check_rapid_change(self, job_id: int):
        """Check if a job has multiple state changes"""
        if len(self.job_state_timeline[job_id]) > 1:
            self.rapid_change_jobs.add(job_id)
            
    def get_rapid_changes_summary(self) -> dict:
        """
        Get a summary of all rapid changes in the current cycle.
        Returns jobs that had multiple state changes within the monitoring window.
        """
        rapid_changes = {}
        
        for job_id in self.rapid_change_jobs:
            timeline = self.job_state_timeline[job_id]
            if len(timeline) > 1:
                # Get the sequence of actions
                action_sequence = [event['action'] for event in timeline]
                
                # Get the latest job title and owner
                latest_event = timeline[-1]
                job_title = latest_event.get('title', f'Job {job_id}')
                owner = latest_event.get('owner', 'Unknown')
                
                # Calculate time span of changes
                first_time = timeline[0]['timestamp']
                last_time = timeline[-1]['timestamp']
                time_span_seconds = (last_time - first_time).total_seconds()
                
                rapid_changes[job_id] = {
                    'job_id': job_id,
                    'job_title': job_title,
                    'owner': owner,
                    'action_sequence': action_sequence,
                    'action_count': len(timeline),
                    'time_span_seconds': time_span_seconds,
                    'timeline': timeline,
                    'summary': self._generate_change_summary(timeline)
                }
                
        return rapid_changes
        
    def _generate_change_summary(self, timeline: list) -> str:
        """Generate a human-readable summary of the changes"""
        actions = [event['action'] for event in timeline]
        
        if len(actions) == 1:
            return f"Job was {actions[0]}"
        elif len(actions) == 2:
            return f"Job was {actions[0]} then {actions[1]}"
        else:
            # More complex sequence
            summary_parts = []
            for i, action in enumerate(actions):
                if i == 0:
                    summary_parts.append(f"initially {action}")
                elif i == len(actions) - 1:
                    summary_parts.append(f"finally {action}")
                else:
                    summary_parts.append(f"then {action}")
            return "Job was " + ", ".join(summary_parts)
            
    def get_all_changes_for_notification(self) -> dict:
        """
        Get all changes (both rapid and normal) formatted for notification.
        This provides a complete picture of all activity in the cycle.
        """
        all_changes = {
            'rapid_changes': self.get_rapid_changes_summary(),
            'total_jobs_affected': len(self.job_state_timeline),
            'jobs_with_multiple_changes': len(self.rapid_change_jobs),
            'cycle_duration_seconds': (datetime.utcnow() - self.cycle_start_time).total_seconds() if self.cycle_start_time else 0,
            'detailed_timeline': dict(self.job_state_timeline)
        }
        
        return all_changes
        
    def has_rapid_changes(self) -> bool:
        """Check if there are any rapid changes in the current cycle"""
        return len(self.rapid_change_jobs) > 0
        
    def get_rapid_change_alert_message(self) -> str:
        """Generate an alert message for rapid changes"""
        if not self.has_rapid_changes():
            return ""
            
        rapid_summary = self.get_rapid_changes_summary()
        alert_lines = [
            "⚠️ RAPID CHANGES DETECTED ⚠️",
            f"The following {len(rapid_summary)} job(s) had multiple state changes within this monitoring cycle:",
            ""
        ]
        
        for job_id, info in rapid_summary.items():
            alert_lines.append(f"• Job #{job_id} - {info['job_title']} (Owner: {info['owner']})")
            alert_lines.append(f"  {info['summary']}")
            alert_lines.append(f"  ({info['action_count']} changes in {info['time_span_seconds']:.1f} seconds)")
            alert_lines.append("")
            
        return "\n".join(alert_lines)