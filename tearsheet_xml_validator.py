"""
Tearsheet to XML Validation Service
Ensures all jobs in tearsheets are properly reflected in XML files
"""

import logging
from typing import Dict, List, Tuple
from xml_integration_service import XMLIntegrationService

class TearsheetXMLValidator:
    """Validates that all tearsheet jobs are present in XML files"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.xml_service = XMLIntegrationService()
    
    def validate_tearsheet_sync(self, bullhorn_service, monitors) -> Tuple[bool, str]:
        """
        Validate that all tearsheet jobs are in the XML file.
        This is the CRITICAL fallback to ensure consistency.
        
        Returns:
            Tuple[bool, str]: (is_valid, summary_message)
        """
        try:
            # Get all current jobs from tearsheets
            all_tearsheet_jobs = {}
            tearsheet_summary = []
            
            for monitor in monitors:
                if monitor.tearsheet_id == 0:
                    jobs = bullhorn_service.get_jobs_by_query(monitor.tearsheet_name)
                else:
                    jobs = bullhorn_service.get_tearsheet_jobs(monitor.tearsheet_id)
                    
                tearsheet_summary.append(f"{monitor.name}: {len(jobs)} jobs")
                    
                for job in jobs:
                    job_id = str(job.get('id'))
                    all_tearsheet_jobs[job_id] = {
                        'job': job,
                        'tearsheet': monitor.name
                    }
            
            total_tearsheet_jobs = len(all_tearsheet_jobs)
            self.logger.info(f"Tearsheet validation: {', '.join(tearsheet_summary)}")
            self.logger.info(f"Total tearsheet jobs: {total_tearsheet_jobs}")
            
            # Check current XML
            current_jobs = self.xml_service._get_current_jobs()
            xml_job_count = len(current_jobs)
            self.logger.info(f"Current XML jobs: {xml_job_count}")
            
            # Find missing jobs
            missing_jobs = []
            for job_id, job_data in all_tearsheet_jobs.items():
                if job_id not in current_jobs:
                    missing_jobs.append(job_data)
            
            if missing_jobs:
                self.logger.warning(f"CRITICAL: Found {len(missing_jobs)} jobs missing from XML!")
                
                # Add all missing jobs
                added_count = 0
                for job_data in missing_jobs:
                    try:
                        self.xml_service.add_job_to_xml(job_data['job'])
                        self.logger.info(f"Added missing job {job_data['job']['id']}: {job_data['job'].get('title', 'Unknown')}")
                        added_count += 1
                    except Exception as e:
                        self.logger.error(f"Failed to add job {job_data['job']['id']}: {str(e)}")
                
                summary = f"FIXED: Added {added_count}/{len(missing_jobs)} missing jobs. XML now has {xml_job_count + added_count}/{total_tearsheet_jobs} jobs"
                return added_count > 0, summary
                
            elif xml_job_count < total_tearsheet_jobs:
                # Edge case: XML has fewer jobs but no specific jobs are missing
                summary = f"WARNING: XML has {xml_job_count} jobs but tearsheets have {total_tearsheet_jobs}"
                self.logger.warning(summary)
                return False, summary
                
            else:
                summary = f"✅ Validation passed: All {total_tearsheet_jobs} tearsheet jobs are in XML"
                self.logger.info(summary)
                return True, summary
                
        except Exception as e:
            error_msg = f"Validation error: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg