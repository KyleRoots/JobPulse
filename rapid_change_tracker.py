"""
RapidChangeTracker - Simple implementation for monitoring job state transitions
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Set
import logging

class RapidChangeTracker:
    """Tracks rapid changes in job states to prevent excessive notifications"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.cycle_start_time = None
        self.job_states = {}  # job_id -> state
        self.state_transitions = {}  # job_id -> list of transitions
        self.rapid_change_threshold = 3  # Number of changes to consider "rapid"
        self.rapid_change_window = timedelta(minutes=30)  # Time window
    
    def start_new_cycle(self):
        """Start a new monitoring cycle"""
        self.cycle_start_time = datetime.utcnow()
        self.logger.debug(f"Started new rapid change tracking cycle at {self.cycle_start_time}")
    
    def track_job_change(self, job_id: str, old_state: str, new_state: str):
        """Track a job state change"""
        if job_id not in self.state_transitions:
            self.state_transitions[job_id] = []
        
        self.state_transitions[job_id].append({
            'timestamp': datetime.utcnow(),
            'old_state': old_state,
            'new_state': new_state
        })
        
        self.job_states[job_id] = new_state
        
        # Clean old transitions outside the window
        self._clean_old_transitions(job_id)
    
    def _clean_old_transitions(self, job_id: str):
        """Remove transitions older than the tracking window"""
        if job_id in self.state_transitions:
            cutoff_time = datetime.utcnow() - self.rapid_change_window
            self.state_transitions[job_id] = [
                t for t in self.state_transitions[job_id] 
                if t['timestamp'] > cutoff_time
            ]
    
    def is_rapid_change(self, job_id: str) -> bool:
        """Check if a job has rapid changes"""
        if job_id not in self.state_transitions:
            return False
        
        recent_transitions = len(self.state_transitions[job_id])
        return recent_transitions >= self.rapid_change_threshold
    
    def get_rapid_change_jobs(self) -> List[str]:
        """Get list of jobs with rapid changes"""
        rapid_jobs = []
        for job_id in self.state_transitions:
            if self.is_rapid_change(job_id):
                rapid_jobs.append(job_id)
        return rapid_jobs
    
    def should_suppress_notification(self, job_id: str) -> bool:
        """Check if notifications should be suppressed for rapid changes"""
        return self.is_rapid_change(job_id)
    
    def get_summary(self) -> Dict:
        """Get summary of tracking data"""
        return {
            'cycle_start': self.cycle_start_time,
            'tracked_jobs': len(self.job_states),
            'rapid_change_jobs': len(self.get_rapid_change_jobs()),
            'total_transitions': sum(len(transitions) for transitions in self.state_transitions.values())
        }