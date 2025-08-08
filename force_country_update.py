#!/usr/bin/env python3
"""
Force update XML files with country names from Bullhorn
"""

import logging
import os
from datetime import datetime
from lxml import etree

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Force update all jobs with country names"""
    from app import app
    
    with app.app_context():
        try:
            logger.info("=" * 80)
            logger.info("FORCING COUNTRY FIELD UPDATE IN XML FILES")
            logger.info("=" * 80)
            
            # Import services
            from bullhorn_service import BullhornService
            from xml_integration_service import XMLIntegrationService
            
            # Initialize services
            bullhorn = BullhornService()
            xml_service = XMLIntegrationService()
            
            # Authenticate with Bullhorn
            logger.info("\nAuthenticating with Bullhorn...")
            bullhorn.authenticate()
            logger.info("✅ Authentication successful")
            
            # Get all jobs from tearsheets
            logger.info("\nFetching all jobs from tearsheets...")
            
            tearsheets = {
                1256: "Ottawa Sponsored Jobs",
                1264: "VMS Sponsored Jobs", 
                1499: "Clover Sponsored Jobs",
                1257: "Chicago Sponsored Jobs",
                1258: "Cleveland Sponsored Jobs"
            }
            
            all_jobs = []
            job_country_map = {}  # Map job ID to country name
            
            for tearsheet_id, name in tearsheets.items():
                logger.info(f"Fetching jobs from {name} (ID: {tearsheet_id})...")
                jobs = bullhorn.get_tearsheet_jobs(tearsheet_id)
                if jobs:
                    logger.info(f"  Found {len(jobs)} jobs")
                    for job in jobs:
                        job_id = str(job.get('id'))
                        all_jobs.append(job)
                        
                        # Extract country name from address
                        address = job.get('address', {})
                        country_name = address.get('countryName', 'United States') if address else 'United States'
                        job_country_map[job_id] = country_name
                        
                        # Log sample country data for first few jobs
                        if len(job_country_map) <= 3:
                            logger.info(f"    Job {job_id}: Country = '{country_name}'")
                else:
                    logger.info(f"  No jobs found")
            
            logger.info(f"\n✅ Total jobs found: {len(all_jobs)}")
            logger.info(f"Country mapping created for {len(job_country_map)} jobs")
            
            # Update each XML file
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            
            for xml_file in xml_files:
                if not os.path.exists(xml_file):
                    logger.warning(f"  {xml_file} not found, skipping...")
                    continue
                    
                logger.info(f"\n{'='*50}")
                logger.info(f"Updating {xml_file}...")
                
                # Backup the file first
                backup_file = f"{xml_file}.backup_country_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                with open(xml_file, 'r') as f:
                    content = f.read()
                with open(backup_file, 'w') as f:
                    f.write(content)
                logger.info(f"  Created backup: {backup_file}")
                
                # Parse XML
                try:
                    tree = etree.parse(xml_file)
                    root = tree.getroot()
                    
                    updated_count = 0
                    
                    # Update each job's country field
                    for job_elem in root.findall('.//job'):
                        referencenumber_elem = job_elem.find('.//referencenumber')
                        country_elem = job_elem.find('.//country')
                        
                        if referencenumber_elem is not None and country_elem is not None:
                            ref_number = referencenumber_elem.text.strip() if referencenumber_elem.text else ""
                            
                            # Extract job ID from reference number (format: BH-XXXXX)
                            if ref_number.startswith('BH-'):
                                job_id = ref_number[3:]  # Remove 'BH-' prefix
                                
                                if job_id in job_country_map:
                                    new_country = job_country_map[job_id]
                                    old_country = country_elem.text.strip() if country_elem.text else ""
                                    
                                    # Update if it's a numeric ID or different value
                                    if old_country.isdigit() or old_country != new_country:
                                        country_elem.text = f" {new_country} "
                                        updated_count += 1
                                        
                                        if updated_count <= 5:  # Log first 5 updates
                                            logger.info(f"    Updated job {job_id}: '{old_country}' → '{new_country}'")
                    
                    # Write updated XML
                    tree.write(xml_file, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    logger.info(f"  ✅ Updated {updated_count} jobs with country names")
                    
                    # Verify the update
                    with open(xml_file, 'r') as f:
                        content = f.read()
                        import re
                        countries = re.findall(r'<country>(.*?)</country>', content)
                        if countries:
                            numeric_countries = [c for c in countries if c.strip().isdigit()]
                            if numeric_countries:
                                logger.warning(f"  ⚠️ Still have {len(numeric_countries)} numeric country IDs")
                            else:
                                logger.info(f"  ✅ All country fields now use names!")
                                
                except Exception as e:
                    logger.error(f"  Error processing {xml_file}: {str(e)}")
            
            logger.info("\n" + "=" * 80)
            logger.info("COUNTRY FIELD UPDATE COMPLETE")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()