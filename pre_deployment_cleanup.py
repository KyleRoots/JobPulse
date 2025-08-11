#!/usr/bin/env python3
"""
Pre-deployment cleanup script to optimize the system for deployment
"""
import os
import glob
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def cleanup_old_backups():
    """Remove old backup files, keeping only the most recent ones"""
    logger.info("=== CLEANING UP OLD BACKUP FILES ===")
    
    # Remove old XML backup files (keep only last 3 of each type)
    backup_patterns = [
        "myticas-job-feed*.xml.backup_*",
        "myticas-job-feed-scheduled*.xml.backup_*"
    ]
    
    removed_count = 0
    for pattern in backup_patterns:
        backup_files = glob.glob(pattern)
        if len(backup_files) > 3:
            # Sort by modification time and remove older ones
            backup_files.sort(key=os.path.getmtime)
            files_to_remove = backup_files[:-3]  # Keep last 3
            
            for file_path in files_to_remove:
                try:
                    os.remove(file_path)
                    logger.info(f"Removed old backup: {file_path}")
                    removed_count += 1
                except Exception as e:
                    logger.error(f"Failed to remove {file_path}: {e}")
    
    # Clean up very old xml_backups directory files (older than 7 days)
    xml_backup_dir = "xml_backups"
    if os.path.exists(xml_backup_dir):
        cutoff_date = datetime.now() - timedelta(days=7)
        for file in os.listdir(xml_backup_dir):
            file_path = os.path.join(xml_backup_dir, file)
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if file_time < cutoff_date:
                    try:
                        os.remove(file_path)
                        logger.info(f"Removed old archive backup: {file_path}")
                        removed_count += 1
                    except Exception as e:
                        logger.error(f"Failed to remove {file_path}: {e}")
    
    logger.info(f"Removed {removed_count} old backup files")
    return removed_count

def cleanup_debug_scripts():
    """Remove temporary and debug scripts"""
    logger.info("=== CLEANING UP DEBUG/TEMPORARY SCRIPTS ===")
    
    debug_scripts = [
        "debug_job_32539.py",
        "fix_job_32539_immediately.py",
        "manual_corruption_fix.py",
        "quick_orphan_fix.py",
        "test_html_fix.py",
        "simple_ftp_test.py",
        "monitor_live_cycle.py"  # Created during debugging
    ]
    
    removed_count = 0
    for script in debug_scripts:
        if os.path.exists(script):
            try:
                os.remove(script)
                logger.info(f"Removed debug script: {script}")
                removed_count += 1
            except Exception as e:
                logger.error(f"Failed to remove {script}: {e}")
    
    logger.info(f"Removed {removed_count} debug scripts")
    return removed_count

def cleanup_temporary_files():
    """Remove temporary and broken files"""
    logger.info("=== CLEANING UP TEMPORARY FILES ===")
    
    temp_files = [
        "myticas-job-feed.xml.broken_format",
        "temp_check.xml",
        "live_xml_*.xml"  # Various live XML debug files
    ]
    
    removed_count = 0
    
    # Remove specific files
    specific_files = [
        "myticas-job-feed.xml.broken_format", 
        "temp_check.xml"
    ]
    
    for file in specific_files:
        if os.path.exists(file):
            try:
                os.remove(file)
                logger.info(f"Removed temporary file: {file}")
                removed_count += 1
            except Exception as e:
                logger.error(f"Failed to remove {file}: {e}")
    
    # Remove live_xml_* files
    live_xml_files = glob.glob("live_xml_*.xml")
    for file in live_xml_files:
        try:
            os.remove(file)
            logger.info(f"Removed live XML file: {file}")
            removed_count += 1
        except Exception as e:
            logger.error(f"Failed to remove {file}: {e}")
    
    logger.info(f"Removed {removed_count} temporary files")
    return removed_count

def cleanup_old_logs():
    """Clean up old log files"""
    logger.info("=== CLEANING UP OLD LOG FILES ===")
    
    removed_count = 0
    
    # Remove app.log.backup if it exists (we have app.log)
    if os.path.exists("app.log.backup"):
        try:
            os.remove("app.log.backup")
            logger.info("Removed app.log.backup")
            removed_count += 1
        except Exception as e:
            logger.error(f"Failed to remove app.log.backup: {e}")
    
    logger.info(f"Removed {removed_count} old log files")
    return removed_count

def cleanup_html_test_files():
    """Remove HTML test files"""
    logger.info("=== CLEANING UP TEST HTML FILES ===")
    
    test_files = [
        "scheduler_debug.html",
        "scheduler_test.html"
    ]
    
    removed_count = 0
    for file in test_files:
        if os.path.exists(file):
            try:
                os.remove(file)
                logger.info(f"Removed test file: {file}")
                removed_count += 1
            except Exception as e:
                logger.error(f"Failed to remove {file}: {e}")
    
    logger.info(f"Removed {removed_count} test HTML files")
    return removed_count

def verify_essential_files():
    """Verify that essential production files are still present"""
    logger.info("=== VERIFYING ESSENTIAL FILES ===")
    
    essential_files = [
        "main.py",
        "app.py", 
        "models.py",
        "bullhorn_service.py",
        "xml_integration_service.py",
        "comprehensive_monitoring_service.py",
        "ftp_service.py",
        "email_service.py",
        "replit.md",
        "pyproject.toml",
        "myticas-job-feed.xml"
    ]
    
    missing_files = []
    for file in essential_files:
        if not os.path.exists(file):
            missing_files.append(file)
    
    if missing_files:
        logger.error(f"⚠️ Missing essential files: {missing_files}")
        return False
    else:
        logger.info("✅ All essential files present")
        return True

def main():
    """Run comprehensive pre-deployment cleanup"""
    logger.info("🧹 STARTING PRE-DEPLOYMENT CLEANUP")
    logger.info("=" * 50)
    
    total_removed = 0
    
    # Run all cleanup operations
    total_removed += cleanup_old_backups()
    total_removed += cleanup_debug_scripts()
    total_removed += cleanup_temporary_files()
    total_removed += cleanup_old_logs()
    total_removed += cleanup_html_test_files()
    
    # Verify essential files
    if not verify_essential_files():
        logger.error("❌ CLEANUP FAILED: Essential files missing!")
        return False
    
    logger.info("=" * 50)
    logger.info(f"🎉 CLEANUP COMPLETE: Removed {total_removed} files")
    logger.info("✅ System optimized for deployment")
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)