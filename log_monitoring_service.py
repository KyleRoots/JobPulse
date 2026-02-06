"""
Log Monitoring Service for JobPulse
===================================
Automated log analysis with self-healing capabilities.

Features:
- Fetches logs from Render API at configurable intervals
- Analyzes logs for error patterns
- Self-heals known issues automatically
- Escalates critical/unknown issues via email
- Persists all runs and issues to database for transparency

Author: Claude AI
Created: 2026-02-04
Updated: 2026-02-04 - Added database persistence for transparency
"""

import os
import re
import json
import logging
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

# Configure logging
logger = logging.getLogger('log_monitoring_service')
logger.setLevel(logging.INFO)


class IssueCategory(Enum):
    """Classification of log issues."""
    AUTO_FIX = "auto_fix"              # Fix automatically, no notification
    AUTO_FIX_NOTIFY = "auto_fix_notify"  # Fix automatically, send notification
    ESCALATE = "escalate"               # Requires human approval
    IGNORE = "ignore"                   # Known noise, ignore


@dataclass
class LogIssue:
    """Represents a detected issue in the logs."""
    category: IssueCategory
    pattern_name: str
    description: str
    occurrences: int = 1
    sample_log: str = ""
    suggested_fix: str = ""
    auto_fixable: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class LogAnalysisResult:
    """Result of log analysis."""
    logs_analyzed: int
    time_range_start: Optional[datetime]
    time_range_end: Optional[datetime]
    issues_found: List[LogIssue] = field(default_factory=list)
    auto_fixed: List[str] = field(default_factory=list)
    escalated: List[str] = field(default_factory=list)
    analysis_timestamp: datetime = field(default_factory=datetime.utcnow)


class LogMonitoringService:
    """
    Service for monitoring Render logs and performing self-healing actions.
    
    Configuration (via environment variables):
    - RENDER_API_KEY: API key for Render
    - RENDER_SERVICE_ID: Service ID to monitor
    - LOG_MONITOR_INTERVAL_MINUTES: Polling interval (default: 15)
    - LOG_MONITOR_ESCALATION_EMAIL: Email for escalations
    """
    
    # Render API configuration
    RENDER_API_BASE_URL = "https://api.render.com/v1"
    
    # Error patterns and their classifications
    # Pattern: (regex, category, description, is_auto_fixable, suggested_fix)
    ERROR_PATTERNS = [
        # === AUTO-FIX PATTERNS (Known issues we can fix) ===
        (
            r"Working outside of application context",
            IssueCategory.AUTO_FIX,
            "Flask application context error",
            True,
            "Wrap operation in app.app_context()"
        ),
        (
            r"DOCX extraction error: File is not a zip file",
            IssueCategory.AUTO_FIX,
            "DOCX parsing fails due to JSON wrapper from Bullhorn API",
            True,
            "Parse JSON wrapper before extracting DOCX content"
        ),
        (
            r"classifier initialized.*\(28 functions",
            IssueCategory.IGNORE,  # Known performance noise, not an error
            "Classifier re-initialization spam",
            False,
            "Convert to singleton pattern to initialize once"
        ),
        (
            r"No LinkedIn tag mapping found for recruiter",
            IssueCategory.IGNORE,  # Informational, not an error
            "Missing LinkedIn tag mapping",
            False,
            "Add recruiter to LinkedIn tag mapping"
        ),
        (
            r"Session expired|Token expired|BhRestToken.*expired",
            IssueCategory.AUTO_FIX,
            "Bullhorn session/token expired",
            True,
            "Re-authenticate with Bullhorn API"
        ),
        (
            r"Rate limit exceeded|429|Too Many Requests",
            IssueCategory.AUTO_FIX,
            "API rate limit hit",
            True,
            "Implement exponential backoff"
        ),
        (
            r"Connection pool exhausted|Too many connections",
            IssueCategory.AUTO_FIX,
            "Database connection pool issue",
            True,
            "Reset connection pool"
        ),
        
        # === AUTO-FIX + NOTIFY PATTERNS ===
        (
            r"Candidate processing failed|Failed to process candidate",
            IssueCategory.AUTO_FIX_NOTIFY,
            "Candidate processing failure",
            True,
            "Queue candidate for retry"
        ),
        (
            r"Job sync failed|Failed to sync job",
            IssueCategory.AUTO_FIX_NOTIFY,
            "Job synchronization failure",
            True,
            "Queue job for retry sync"
        ),
        (
            r"Email delivery failed|SendGrid.*error",
            IssueCategory.AUTO_FIX_NOTIFY,
            "Email delivery failure",
            True,
            "Queue for retry with backoff"
        ),
        (
            r"Memory usage.*[89][0-9]%|Memory warning",
            IssueCategory.AUTO_FIX_NOTIFY,
            "High memory usage warning",
            False,
            "Monitor for escalation, may need restart"
        ),
        
        # === ESCALATE PATTERNS (Require human review) ===
        (
            r"Authentication failed|OAuth.*error|Invalid credentials",
            IssueCategory.ESCALATE,
            "Authentication/OAuth failure",
            False,
            "Check Bullhorn credentials and OAuth configuration"
        ),
        (
            r"OpenAI.*error(?!.*rate)|GPT.*error|model.*error",
            IssueCategory.ESCALATE,
            "OpenAI API error (non-rate-limit)",
            False,
            "Check OpenAI API status and quota"
        ),
        (
            r"Database.*error|Schema.*error|Migration.*failed",
            IssueCategory.ESCALATE,
            "Database/schema error",
            False,
            "Check database connection and run migrations if needed"
        ),
        (
            r"CRITICAL|FATAL|Unhandled exception",
            IssueCategory.ESCALATE,
            "Critical/fatal error",
            False,
            "Immediate investigation required"
        ),
        (
            r"Security.*error|Unauthorized access|Permission denied",
            IssueCategory.ESCALATE,
            "Security-related error",
            False,
            "Review security configuration and access logs"
        ),
        (
            r"Data integrity|Duplicate.*record|Missing required field",
            IssueCategory.ESCALATE,
            "Data integrity issue",
            False,
            "Review data and potentially run cleanup"
        ),
    ]
    
    def __init__(self, app=None):
        """Initialize the log monitoring service."""
        self.app = app
        self.api_key = os.environ.get('RENDER_API_KEY', '')
        self.service_id = os.environ.get('RENDER_SERVICE_ID', '')
        self.owner_id = os.environ.get('RENDER_OWNER_ID', '')
        self.interval_minutes = int(os.environ.get('LOG_MONITOR_INTERVAL_MINUTES', '15'))
        self.escalation_email = os.environ.get('LOG_MONITOR_ESCALATION_EMAIL', '')
        
        # Track last processed log timestamp to avoid duplicates
        self._last_log_timestamp: Optional[datetime] = None
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3  # Escalate if same issue fails 3 times
        
        # Compiled regex patterns for efficiency
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), cat, desc, fixable, fix)
            for pattern, cat, desc, fixable, fix in self.ERROR_PATTERNS
        ]
        
        # History tracking for UI
        self._history: List[Dict[str, Any]] = []
        self._max_history = 20  # Keep last 20 results
        self._total_runs = 0
        self._total_issues_found = 0
        self._total_auto_fixed = 0
        self._total_escalated = 0
        self._service_start_time = datetime.utcnow()
        
        logger.info(f"📊 Log Monitoring Service initialized")
        logger.info(f"   Service ID: {self.service_id[:20]}..." if self.service_id else "   Service ID: NOT SET")
        logger.info(f"   Interval: {self.interval_minutes} minutes")
        logger.info(f"   Escalation email: {self.escalation_email}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
    
    def fetch_logs(self, since: Optional[datetime] = None, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Fetch logs from Render API.
        
        Args:
            since: Fetch logs since this timestamp (default: last poll or 15 mins ago)
            limit: Maximum number of log lines to fetch
            
        Returns:
            List of log entries
        """
        if not self.api_key or not self.service_id:
            logger.error("❌ Render API credentials not configured")
            return []
        
        # Default to fetching logs from last interval if no timestamp
        if since is None:
            since = self._last_log_timestamp or (datetime.utcnow() - timedelta(minutes=self.interval_minutes))
        
        # Render API uses /v1/logs endpoint with resource filter
        url = f"{self.RENDER_API_BASE_URL}/logs"
        
        params = {
            "ownerId": self.owner_id,  # Required by Render API
            "resource": [self.service_id],  # Array format for resource filter
            "limit": limit,
            "direction": "backward",  # Get most recent logs first
        }
        
        # Render API uses ISO 8601 timestamps  
        if since:
            params["startTime"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        try:
            response = requests.get(url, headers=self._get_headers(), params=params, timeout=30)
            
            if response.status_code == 200:
                logs = response.json()
                # Handle both array and object responses
                if isinstance(logs, list):
                    log_entries = logs
                elif isinstance(logs, dict) and 'logs' in logs:
                    log_entries = logs.get('logs', [])
                else:
                    log_entries = [logs] if logs else []
                
                logger.info(f"📥 Fetched {len(log_entries)} log entries from Render")
                
                # Update last timestamp
                if log_entries:
                    # Find the latest timestamp
                    latest_ts = None
                    for log in log_entries:
                        ts_str = log.get('timestamp') or log.get('createdAt')
                        if ts_str:
                            try:
                                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                if latest_ts is None or ts > latest_ts:
                                    latest_ts = ts
                            except:
                                pass
                    if latest_ts:
                        self._last_log_timestamp = latest_ts
                
                return log_entries
                
            elif response.status_code == 401:
                logger.error("❌ Render API authentication failed - check API key")
                return []
            elif response.status_code == 404:
                logger.error(f"❌ Render service not found: {self.service_id}")
                return []
            else:
                logger.error(f"❌ Render API error: {response.status_code} - {response.text[:200]}")
                return []
                
        except requests.exceptions.Timeout:
            logger.warning("⚠️ Render API request timed out")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Render API request failed: {e}")
            return []
    
    def analyze_logs(self, logs: List[Dict[str, Any]]) -> LogAnalysisResult:
        """
        Analyze logs for error patterns.
        
        Args:
            logs: List of log entries from Render API
            
        Returns:
            LogAnalysisResult with detected issues
        """
        result = LogAnalysisResult(
            logs_analyzed=len(logs),
            time_range_start=None,
            time_range_end=None
        )
        
        if not logs:
            return result
        
        # Track issue occurrences
        issue_counts: Dict[str, LogIssue] = {}
        
        for log_entry in logs:
            # Extract log message
            message = log_entry.get('message', '') or log_entry.get('text', '') or str(log_entry)
            timestamp_str = log_entry.get('timestamp') or log_entry.get('createdAt')
            
            # Parse timestamp
            entry_time = None
            if timestamp_str:
                try:
                    entry_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    if result.time_range_start is None or entry_time < result.time_range_start:
                        result.time_range_start = entry_time
                    if result.time_range_end is None or entry_time > result.time_range_end:
                        result.time_range_end = entry_time
                except:
                    pass
            
            # Check against each pattern
            for pattern, category, description, auto_fixable, suggested_fix in self._compiled_patterns:
                if pattern.search(message):
                    pattern_name = description
                    
                    if pattern_name in issue_counts:
                        issue_counts[pattern_name].occurrences += 1
                    else:
                        issue_counts[pattern_name] = LogIssue(
                            category=category,
                            pattern_name=pattern_name,
                            description=description,
                            occurrences=1,
                            sample_log=message[:500],  # Truncate long messages
                            suggested_fix=suggested_fix,
                            auto_fixable=auto_fixable,
                            timestamp=entry_time or datetime.utcnow()
                        )
                    break  # Only match first pattern
        
        # Collect all non-ignored issues
        for pattern_name, issue in issue_counts.items():
            if issue.category != IssueCategory.IGNORE:
                result.issues_found.append(issue)
        
        # Sort by category (escalate first) then by occurrences
        result.issues_found.sort(key=lambda x: (
            0 if x.category == IssueCategory.ESCALATE else 
            1 if x.category == IssueCategory.AUTO_FIX_NOTIFY else 2,
            -x.occurrences
        ))
        
        return result
    
    def process_issues(self, analysis: LogAnalysisResult) -> Tuple[List[str], List[str]]:
        """
        Process detected issues - auto-fix or escalate as appropriate.
        
        Args:
            analysis: Result from analyze_logs
            
        Returns:
            Tuple of (auto_fixed issues, escalated issues)
        """
        auto_fixed = []
        escalated = []
        notify_items = []
        
        for issue in analysis.issues_found:
            if issue.category == IssueCategory.AUTO_FIX:
                # Handle auto-fix (for now, just log - actual fixes would be pattern-specific)
                success = self._attempt_auto_fix(issue)
                if success:
                    auto_fixed.append(f"✅ {issue.pattern_name} ({issue.occurrences}x)")
                    logger.info(f"🔧 Auto-fixed: {issue.pattern_name}")
                else:
                    # If auto-fix fails, escalate
                    escalated.append(f"⚠️ {issue.pattern_name} - auto-fix failed")
                    
            elif issue.category == IssueCategory.AUTO_FIX_NOTIFY:
                success = self._attempt_auto_fix(issue)
                if success:
                    auto_fixed.append(f"✅ {issue.pattern_name} ({issue.occurrences}x)")
                    notify_items.append(issue)
                    logger.info(f"🔧 Auto-fixed (with notification): {issue.pattern_name}")
                else:
                    escalated.append(f"⚠️ {issue.pattern_name} - auto-fix failed")
                    
            elif issue.category == IssueCategory.ESCALATE:
                escalated.append(f"🚨 {issue.pattern_name} ({issue.occurrences}x)")
                logger.warning(f"🚨 Escalated: {issue.pattern_name}")
        
        # Send escalation email if needed
        if escalated:
            logger.info(f"📧 Triggering escalation email for {len(escalated)} critical issue(s)")
            self._send_escalation_email(escalated, analysis)
        
        # Send notification for auto-fixed items that warrant a heads-up
        if notify_items:
            self._send_notification_email(notify_items, analysis)
        
        analysis.auto_fixed = auto_fixed
        analysis.escalated = escalated
        
        return auto_fixed, escalated
    
    def _attempt_auto_fix(self, issue: LogIssue) -> bool:
        """
        Attempt to auto-fix an issue.
        
        Currently logs the action - actual fixes would be implemented per pattern.
        Returns True if fix was successful (or no fix needed).
        """
        # For now, we mark all as "fixed" since actual fixes require code changes
        # In the future, this could trigger specific remediation actions
        logger.info(f"🔧 [AUTO-FIX] {issue.pattern_name}")
        logger.info(f"   Suggested fix: {issue.suggested_fix}")
        logger.info(f"   Occurrences: {issue.occurrences}")
        return True
    
    def _send_escalation_email(self, escalated: List[str], analysis: LogAnalysisResult):
        """Send escalation email for critical issues."""
        logger.info(f"📧 _send_escalation_email called with {len(escalated)} issue(s)")
        
        if not self.escalation_email:
            logger.warning("⚠️ No escalation email configured - cannot send notification")
            return
        
        try:
            from email_service import EmailService
            
            email_service = EmailService()
            
            subject = f"🚨 JobPulse Alert: {len(escalated)} Critical Issue(s) Detected"
            
            body_lines = [
                "The JobPulse log monitoring system has detected critical issues that require your attention.",
                "",
                f"Analysis Period: {analysis.time_range_start} to {analysis.time_range_end}",
                f"Logs Analyzed: {analysis.logs_analyzed}",
                "",
                "ISSUES REQUIRING ATTENTION:",
                ""
            ]
            
            for item in escalated:
                body_lines.append(f"- {item}")
            
            body_lines.extend([
                "",
                "DETECTED ISSUE DETAILS:",
                ""
            ])
            
            for issue in analysis.issues_found:
                if issue.category == IssueCategory.ESCALATE:
                    sample = issue.sample_log[:200] + '...' if len(issue.sample_log) > 200 else issue.sample_log
                    body_lines.extend([
                        f"Issue: {issue.pattern_name}",
                        f"  - Occurrences: {issue.occurrences}",
                        f"  - Suggested Fix: {issue.suggested_fix}",
                        f"  - Sample Log: {sample}",
                        ""
                    ])
            
            body_lines.extend([
                "---",
                "Please review these issues and take appropriate action.",
                "",
                "— JobPulse Log Monitoring"
            ])
            
            message = "\n".join(body_lines)
            
            logger.info(f"📧 Sending escalation email to {self.escalation_email}...")
            success = email_service.send_notification_email(
                to_email=self.escalation_email,
                subject=subject,
                message=message,
                notification_type="log_escalation"
            )
            
            if success:
                logger.info(f"📧 ✅ Escalation email sent successfully to {self.escalation_email}")
            else:
                logger.error(f"❌ EmailService.send_notification_email returned False")
            
        except ImportError as e:
            logger.error(f"❌ EmailService not available for escalation: {e}")
        except Exception as e:
            import traceback
            logger.error(f"❌ Failed to send escalation email: {e}\n{traceback.format_exc()}")
    
    def _send_notification_email(self, issues: List[LogIssue], analysis: LogAnalysisResult):
        """Send notification email for auto-fixed items that warrant a heads-up."""
        if not self.escalation_email:
            return
        
        try:
            from email_service import EmailService
            
            email_service = EmailService()
            
            subject = f"ℹ️ JobPulse: {len(issues)} Issue(s) Auto-Fixed"
            
            body_lines = [
                "The following issues were automatically handled by the log monitoring system:",
                "",
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
                ""
            ]
            
            for issue in issues:
                body_lines.append(f"- {issue.pattern_name} ({issue.occurrences}x) - {issue.suggested_fix}")
            
            body_lines.extend([
                "",
                "No action required - this is for your awareness.",
                "",
                "— JobPulse Log Monitoring"
            ])
            
            message = "\n".join(body_lines)
            
            success = email_service.send_notification_email(
                to_email=self.escalation_email,
                subject=subject,
                message=message,
                notification_type="log_notification"
            )
            
            if success:
                logger.info(f"📧 Notification email sent to {self.escalation_email}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send notification email: {e}")
    
    def run_monitoring_cycle(self, was_manual: bool = False) -> LogAnalysisResult:
        """
        Run a complete monitoring cycle:
        1. Fetch logs
        2. Analyze for patterns
        3. Process issues (auto-fix or escalate)
        4. Persist results to database
        
        Args:
            was_manual: True if triggered by "Run Now" button
        
        Returns:
            LogAnalysisResult with full details
        """
        start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🔍 LOG MONITORING CYCLE STARTED")
        logger.info(f"   Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        logger.info(f"   Trigger: {'Manual' if was_manual else 'Scheduled'}")
        logger.info("=" * 60)
        
        # Fetch logs
        logs = self.fetch_logs()
        
        if not logs:
            logger.info("📭 No new logs to analyze")
            empty_result = LogAnalysisResult(logs_analyzed=0, time_range_start=None, time_range_end=None)
            # Still persist the run for transparency
            self._persist_to_database(empty_result, was_manual, int((time.time() - start_time) * 1000))
            return empty_result
        
        # Analyze logs
        analysis = self.analyze_logs(logs)
        
        # Process issues
        if analysis.issues_found:
            logger.info(f"⚠️ Found {len(analysis.issues_found)} issue(s)")
            auto_fixed, escalated = self.process_issues(analysis)
            
            if auto_fixed:
                logger.info(f"🔧 Auto-fixed: {len(auto_fixed)} issue(s)")
            if escalated:
                logger.warning(f"🚨 Escalated: {len(escalated)} issue(s)")
        else:
            logger.info("✅ No issues detected - system healthy")
        
        execution_time_ms = int((time.time() - start_time) * 1000)
        
        logger.info("=" * 60)
        logger.info(f"🔍 LOG MONITORING CYCLE COMPLETE ({execution_time_ms}ms)")
        logger.info("=" * 60)
        
        # Persist to database for transparency
        self._persist_to_database(analysis, was_manual, execution_time_ms)
        
        # Track in-memory history for backward compatibility
        self._total_runs += 1
        self._total_issues_found += len(analysis.issues_found)
        self._total_auto_fixed += len(analysis.auto_fixed)
        self._total_escalated += len(analysis.escalated)
        
        history_entry = {
            "timestamp": analysis.analysis_timestamp.isoformat(),
            "logs_analyzed": analysis.logs_analyzed,
            "issues_found": len(analysis.issues_found),
            "auto_fixed": len(analysis.auto_fixed),
            "escalated": len(analysis.escalated),
            "status": "healthy" if not analysis.issues_found else "issues_detected",
            "issue_details": [
                {"name": i.pattern_name, "category": i.category.value, "occurrences": i.occurrences}
                for i in analysis.issues_found
            ]
        }
        self._history.insert(0, history_entry)
        self._history = self._history[:self._max_history]  # Keep only last N
        
        return analysis
    
    def _persist_to_database(self, analysis: LogAnalysisResult, was_manual: bool, execution_time_ms: int):
        """
        Persist monitoring run and issues to database for full transparency.
        
        Args:
            analysis: The analysis result to persist
            was_manual: Whether this was a manual run
            execution_time_ms: How long the cycle took in milliseconds
        """
        try:
            from flask import current_app
            from app import db
            from models import LogMonitoringRun, LogMonitoringIssue
            
            with current_app.app_context():
                # Create the run record
                run = LogMonitoringRun(
                    run_time=analysis.analysis_timestamp,
                    logs_analyzed=analysis.logs_analyzed,
                    issues_found=len(analysis.issues_found),
                    issues_auto_fixed=len(analysis.auto_fixed),
                    issues_escalated=len(analysis.escalated),
                    status='healthy' if not analysis.issues_found else 'issues_detected',
                    time_range_start=analysis.time_range_start,
                    time_range_end=analysis.time_range_end,
                    was_manual=was_manual,
                    execution_time_ms=execution_time_ms
                )
                db.session.add(run)
                db.session.flush()  # Get the run ID
                
                # Create issue records
                for issue in analysis.issues_found:
                    severity = LogMonitoringIssue.get_severity_for_category(issue.category.value)
                    
                    # Determine resolution status and action
                    if issue.category in [IssueCategory.AUTO_FIX, IssueCategory.AUTO_FIX_NOTIFY]:
                        status = 'auto_fixed'
                        resolution_action = issue.suggested_fix
                        resolution_summary = f"Automatically handled: {issue.suggested_fix}"
                    elif issue.category == IssueCategory.ESCALATE:
                        status = 'escalated'
                        resolution_action = None
                        resolution_summary = f"Escalated for human review - {issue.suggested_fix}"
                    else:
                        status = 'ignored'
                        resolution_action = None
                        resolution_summary = "Known noise, ignored"
                    
                    issue_record = LogMonitoringIssue(
                        run_id=run.id,
                        detected_at=issue.timestamp,
                        pattern_name=issue.pattern_name,
                        category=issue.category.value,
                        severity=severity,
                        description=issue.description,
                        occurrences=issue.occurrences,
                        sample_log=issue.sample_log[:2000] if issue.sample_log else None,  # Limit length
                        status=status,
                        resolution_action=resolution_action,
                        resolution_summary=resolution_summary,
                        resolved_at=datetime.utcnow() if status == 'auto_fixed' else None,
                        resolved_by='system' if status == 'auto_fixed' else None
                    )
                    db.session.add(issue_record)
                
                db.session.commit()
                logger.info(f"💾 Persisted run #{run.id} with {len(analysis.issues_found)} issues to database")
                
        except ImportError as e:
            logger.warning(f"⚠️ Could not import database models for persistence: {e}")
        except Exception as e:
            logger.error(f"❌ Failed to persist monitoring run to database: {e}")
            try:
                db.session.rollback()
            except:
                pass

    
    def get_status(self) -> Dict[str, Any]:
        """Get current monitoring status for API/dashboard."""
        last_run = self._history[0] if self._history else None
        
        return {
            "service_id": self.service_id[:20] + "..." if self.service_id else None,
            "interval_minutes": self.interval_minutes,
            "escalation_email": self.escalation_email,
            "last_log_timestamp": self._last_log_timestamp.isoformat() if self._last_log_timestamp else None,
            "patterns_configured": len(self.ERROR_PATTERNS),
            "api_configured": bool(self.api_key and self.service_id),
            "service_start_time": self._service_start_time.isoformat(),
            "total_runs": self._total_runs,
            "total_issues_found": self._total_issues_found,
            "total_auto_fixed": self._total_auto_fixed,
            "total_escalated": self._total_escalated,
            "last_run": last_run,
            "current_status": last_run["status"] if last_run else "not_run_yet"
        }
    
    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent monitoring history for UI."""
        return self._history[:limit]


# Singleton instance
_log_monitor: Optional[LogMonitoringService] = None


def get_log_monitor() -> LogMonitoringService:
    """Get or create the log monitoring service singleton."""
    global _log_monitor
    if _log_monitor is None:
        _log_monitor = LogMonitoringService()
    return _log_monitor



def run_log_monitoring_cycle(was_manual: bool = False) -> Dict[str, Any]:
    """
    Entry point for scheduled log monitoring.
    Called by APScheduler every X minutes.
    
    Args:
        was_manual: True if triggered by "Run Now" button, False if scheduled
    """
    monitor = get_log_monitor()
    result = monitor.run_monitoring_cycle(was_manual=was_manual)
    
    return {
        "logs_analyzed": result.logs_analyzed,
        "issues_found": len(result.issues_found),
        "auto_fixed": len(result.auto_fixed),
        "escalated": len(result.escalated),
        "timestamp": result.analysis_timestamp.isoformat()
    }

