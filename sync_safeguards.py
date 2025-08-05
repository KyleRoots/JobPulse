"""
Comprehensive Sync Safeguards to Prevent Job Sync Gaps
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from app import db
import os
import xml.etree.ElementTree as ET

class SyncSafeguards:
    """Enhanced safeguards to prevent sync gaps between tearsheets and XML"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def verify_sync_integrity(self, xml_file_path: str) -> Dict:
        """
        Comprehensive verification that all recent job additions made it to XML
        
        Returns:
            Dict with verification results and any missing jobs
        """
        results = {
            'success': True,
            'missing_jobs': [],
            'verification_errors': [],
            'total_recent_additions': 0
        }
        
        try:
            # Get all XML files to check for sync consistency
            # For now, check if any recent activity indicates jobs should be in XML
            self.logger.info("Checking XML file consistency with recent job activity...")
            
            # For basic implementation, just verify XML file exists and is readable
            results['total_recent_additions'] = 0
            
            # Read current XML content
            if not os.path.exists(xml_file_path):
                results['success'] = False
                results['verification_errors'].append(f"XML file not found: {xml_file_path}")
                return results
            
            with open(xml_file_path, 'r', encoding='utf-8') as f:
                xml_content = f.read()
            
            # Check each recent addition
            for activity in recent_additions:
                job_id = activity.job_id
                
                # Look for job ID in XML (both in bhatsid and title)
                if f"({job_id})" not in xml_content and f"<bhatsid><![CDATA[ {job_id} ]]></bhatsid>" not in xml_content:
                    results['missing_jobs'].append({
                        'job_id': job_id,
                        'added_at': activity.created_at,
                        'details': activity.details
                    })
                    results['success'] = False
                    self.logger.warning(f"SYNC GAP DETECTED: Job {job_id} added at {activity.created_at} but missing from XML")
            
            if results['missing_jobs']:
                self.logger.error(f"Found {len(results['missing_jobs'])} jobs missing from XML after recent additions")
            else:
                self.logger.info(f"Verified {len(recent_additions)} recent job additions are all present in XML")
                
        except Exception as e:
            results['success'] = False
            results['verification_errors'].append(f"Verification failed: {str(e)}")
            self.logger.error(f"Sync verification error: {e}")
        
        return results
    
    def force_sync_recovery(self, missing_jobs: List[Dict]) -> Dict:
        """
        Force recovery for jobs that failed to sync
        
        Args:
            missing_jobs: List of job dictionaries with job_id, added_at, details
            
        Returns:
            Dict with recovery results
        """
        recovery_results = {
            'success': True,
            'recovered_jobs': [],
            'failed_recoveries': [],
            'total_attempts': len(missing_jobs)
        }
        
        try:
            from xml_integration_service import XMLIntegrationService
            from bullhorn_service import get_bullhorn_service
            
            xml_service = XMLIntegrationService()
            bullhorn_service = get_bullhorn_service()
            
            if not bullhorn_service.test_connection():
                recovery_results['success'] = False
                recovery_results['failed_recoveries'].append("Failed to connect to Bullhorn API")
                return recovery_results
            
            for missing_job in missing_jobs:
                job_id = missing_job['job_id']
                
                try:
                    # Get fresh job data from Bullhorn
                    job_data = bullhorn_service.get_job_by_id(job_id)
                    
                    if not job_data:
                        recovery_results['failed_recoveries'].append(f"Job {job_id} not found in Bullhorn")
                        continue
                    
                    # Attempt to add to XML
                    success = xml_service.add_job_to_xml('myticas-job-feed.xml', job_data)
                    
                    if success:
                        recovery_results['recovered_jobs'].append(job_id)
                        self.logger.info(f"Successfully recovered job {job_id} to XML")
                        
                        # Log recovery activity (simplified logging for now)
                        self.logger.info(f"RECOVERY: Successfully recovered job {job_id} after sync gap")
                        
                    else:
                        recovery_results['failed_recoveries'].append(f"Failed to add job {job_id} to XML")
                        recovery_results['success'] = False
                        
                except Exception as e:
                    recovery_results['failed_recoveries'].append(f"Job {job_id} recovery error: {str(e)}")
                    recovery_results['success'] = False
                    self.logger.error(f"Recovery failed for job {job_id}: {e}")
            
            # Commit recovery activities
            db.session.commit()
            
        except Exception as e:
            recovery_results['success'] = False
            recovery_results['failed_recoveries'].append(f"Recovery process error: {str(e)}")
            self.logger.error(f"Force sync recovery error: {e}")
        
        return recovery_results
    
    def run_comprehensive_sync_check(self, xml_file_path: str = 'myticas-job-feed.xml') -> Dict:
        """
        Run complete sync verification and automatic recovery if needed
        
        Returns:
            Dict with complete results
        """
        self.logger.info("Starting comprehensive sync integrity check...")
        
        # Step 1: Verify sync integrity
        verification = self.verify_sync_integrity(xml_file_path)
        
        results = {
            'verification': verification,
            'recovery': None,
            'final_status': 'verified' if verification['success'] else 'sync_gaps_found'
        }
        
        # Step 2: Attempt automatic recovery if gaps found
        if not verification['success'] and verification['missing_jobs']:
            self.logger.warning(f"Attempting automatic recovery of {len(verification['missing_jobs'])} missing jobs...")
            
            recovery = self.force_sync_recovery(verification['missing_jobs'])
            results['recovery'] = recovery
            
            if recovery['success']:
                results['final_status'] = 'recovered'
                self.logger.info(f"Successfully recovered {len(recovery['recovered_jobs'])} jobs")
            else:
                results['final_status'] = 'recovery_failed'
                self.logger.error(f"Recovery failed for some jobs: {recovery['failed_recoveries']}")
        
        return results


def check_sync_safeguards():
    """Standalone function to check sync safeguards - can be called from scheduler"""
    safeguards = SyncSafeguards()
    return safeguards.run_comprehensive_sync_check()


if __name__ == "__main__":
    # Can be run directly for testing
    results = check_sync_safeguards()
    print(f"Sync check results: {results}")