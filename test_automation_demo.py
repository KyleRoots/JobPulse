#!/usr/bin/env python3
"""
Automation Demo Test Script
Demonstrates the complete XML automation workflow with simulated job changes
"""

import os
import sys
import json
import time
import shutil
import logging
from datetime import datetime
from xml_integration_service import XMLIntegrationService
from xml_processor import XMLProcessor
from ftp_service import FTPService

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AutomationDemo:
    def __init__(self):
        self.demo_xml_file = 'demo_automation.xml'
        self.xml_service = XMLIntegrationService()
        self.xml_processor = XMLProcessor()
        
    def create_demo_xml_file(self):
        """Create a demo XML file with initial job data"""
        demo_xml_content = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
  <job>
    <title>
      <![CDATA[ Initial Test Job (88888) ]]>
    </title>
    <company>
      <![CDATA[ Myticas Consulting ]]>
    </company>
    <date>
      <![CDATA[ July 12, 2025 ]]>
    </date>
    <referencenumber><![CDATA[INIT888888]]></referencenumber>
    <url><![CDATA[https://myticas.com/]]></url>
    <description>
      <![CDATA[ This is an initial test job that will remain in the demo. ]]>
    </description>
    <jobtype>
      <![CDATA[ Full-time ]]>
    </jobtype>
    <city>
      <![CDATA[ Chicago ]]>
    </city>
    <state>
      <![CDATA[ Illinois ]]>
    </state>
    <country>
      <![CDATA[ United States ]]>
    </country>
    <category>
      <![CDATA[  ]]>
    </category>
    <apply_email>
      <![CDATA[ apply@myticas.com ]]>
    </apply_email>
    <remotetype>
      <![CDATA[]]>
    </remotetype>
  </job>
</source>'''
        
        with open(self.demo_xml_file, 'w', encoding='utf-8') as f:
            f.write(demo_xml_content)
        
        logger.info(f"✅ Created demo XML file: {self.demo_xml_file}")
        return True
    
    def show_xml_contents(self, title="Current XML Contents"):
        """Display the current XML file contents"""
        logger.info(f"\n📄 {title}")
        logger.info("=" * 50)
        try:
            with open(self.demo_xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract job titles for easy viewing
            import re
            job_titles = re.findall(r'<title>\s*<!\[CDATA\[\s*([^]]+)\s*\]\]>\s*</title>', content)
            
            logger.info(f"Jobs in XML file ({len(job_titles)} total):")
            for i, title in enumerate(job_titles, 1):
                logger.info(f"  {i}. {title.strip()}")
            
            return job_titles
        except Exception as e:
            logger.error(f"Error reading XML file: {str(e)}")
            return []
    
    def simulate_job_changes(self, step_name, previous_jobs, current_jobs):
        """Simulate job changes and show the automation process"""
        logger.info(f"\n🔄 {step_name}")
        logger.info("=" * 50)
        
        # Show what changes will be made
        if not previous_jobs:
            logger.info(f"Adding {len(current_jobs)} new jobs to empty XML file")
        else:
            added_jobs = [job for job in current_jobs if job['id'] not in [pj['id'] for pj in previous_jobs]]
            removed_jobs = [job for job in previous_jobs if job['id'] not in [cj['id'] for cj in current_jobs]]
            
            if added_jobs:
                logger.info(f"Adding {len(added_jobs)} new jobs:")
                for job in added_jobs:
                    logger.info(f"  + {job['title']} (ID: {job['id']})")
            
            if removed_jobs:
                logger.info(f"Removing {len(removed_jobs)} jobs:")
                for job in removed_jobs:
                    logger.info(f"  - {job['title']} (ID: {job['id']})")
            
            if not added_jobs and not removed_jobs:
                logger.info("No changes detected in this step")
                return True
        
        # Perform the XML sync
        sync_result = self.xml_service.sync_xml_with_bullhorn_jobs(
            xml_file_path=self.demo_xml_file,
            current_jobs=current_jobs,
            previous_jobs=previous_jobs
        )
        
        if sync_result.get('success'):
            logger.info(f"✅ XML sync successful: {sync_result}")
            
            # Process the XML file (regenerate reference numbers)
            logger.info("🔄 Processing XML file (regenerating reference numbers)...")
            temp_output = f"{self.demo_xml_file}.processed"
            process_result = self.xml_processor.process_xml(self.demo_xml_file, temp_output)
            
            if process_result.get('success'):
                # Replace original with processed version
                os.replace(temp_output, self.demo_xml_file)
                logger.info(f"✅ XML processing successful: {process_result.get('jobs_processed', 0)} jobs processed")
                
                # Show updated XML contents
                self.show_xml_contents("Updated XML Contents")
                
                return True
            else:
                logger.error(f"❌ XML processing failed: {process_result.get('error')}")
                return False
        else:
            logger.error(f"❌ XML sync failed: {sync_result}")
            return False
    
    def simulate_file_upload(self):
        """Simulate file upload to demonstrate the complete workflow"""
        logger.info("\n📤 Simulating File Upload Process")
        logger.info("=" * 50)
        
        # Create backup of demo file for "upload" simulation
        backup_file = f"{self.demo_xml_file}.backup"
        shutil.copy2(self.demo_xml_file, backup_file)
        
        logger.info(f"✅ File copied to backup location: {backup_file}")
        logger.info("📁 In production, this would upload to:")
        logger.info("   - SFTP server (web server)")
        logger.info("   - Replace automation schedule files")
        logger.info("   - Send email notifications")
        
        # Clean up backup
        os.remove(backup_file)
        
        return True
    
    def run_complete_demo(self):
        """Run the complete automation demo"""
        logger.info("🚀 Starting Complete Automation Demo")
        logger.info("=" * 60)
        
        try:
            # Step 1: Create initial XML file
            logger.info("\n📝 Step 1: Creating Initial XML File")
            if not self.create_demo_xml_file():
                return False
            
            self.show_xml_contents("Initial XML Contents")
            
            # Step 2: Simulate adding new jobs
            logger.info("\n➕ Step 2: Adding New Jobs")
            previous_jobs = []
            current_jobs = [
                {
                    'id': 12345,
                    'title': 'Senior Python Developer',
                    'clientCorporation': {'name': 'Tech Innovations Inc'},
                    'description': 'We are seeking a Senior Python Developer with Django experience...',
                    'address': {'city': 'San Francisco', 'state': 'California', 'countryName': 'United States'},
                    'employmentType': 'Full-time',
                    'dateAdded': 1720742400000,
                    'isOpen': True,
                    'status': 'Open'
                },
                {
                    'id': 67890,
                    'title': 'DevOps Engineer',
                    'clientCorporation': {'name': 'Cloud Solutions LLC'},
                    'description': 'Looking for a DevOps Engineer with AWS and Kubernetes experience...',
                    'address': {'city': 'Seattle', 'state': 'Washington', 'countryName': 'United States'},
                    'employmentType': 'Contract',
                    'dateAdded': 1720742400000,
                    'isOpen': True,
                    'status': 'Open'
                }
            ]
            
            if not self.simulate_job_changes("Adding Jobs to XML", previous_jobs, current_jobs):
                return False
            
            # Step 3: Simulate adding one more job
            logger.info("\n➕ Step 3: Adding Another Job")
            previous_jobs = current_jobs.copy()
            current_jobs.append({
                'id': 11111,
                'title': 'Frontend Developer',
                'clientCorporation': {'name': 'Design Studio Pro'},
                'description': 'Seeking a Frontend Developer with React and TypeScript experience...',
                'address': {'city': 'Austin', 'state': 'Texas', 'countryName': 'United States'},
                'employmentType': 'Full-time',
                'dateAdded': 1720742400000,
                'isOpen': True,
                'status': 'Open'
            })
            
            if not self.simulate_job_changes("Adding Frontend Developer", previous_jobs, current_jobs):
                return False
            
            # Step 4: Simulate removing a job
            logger.info("\n➖ Step 4: Removing a Job")
            previous_jobs = current_jobs.copy()
            current_jobs = [job for job in current_jobs if job['id'] != 67890]  # Remove DevOps Engineer
            
            if not self.simulate_job_changes("Removing DevOps Engineer", previous_jobs, current_jobs):
                return False
            
            # Step 5: Simulate updating a job
            logger.info("\n🔄 Step 5: Updating a Job")
            previous_jobs = current_jobs.copy()
            # Update the Python Developer job
            for job in current_jobs:
                if job['id'] == 12345:
                    job['title'] = 'Senior Python Developer (Updated)'
                    job['description'] = 'Updated: We are seeking a Senior Python Developer with Django and FastAPI experience...'
                    job['address']['city'] = 'San Jose'
                    break
            
            if not self.simulate_job_changes("Updating Python Developer Job", previous_jobs, current_jobs):
                return False
            
            # Step 6: Simulate file upload
            self.simulate_file_upload()
            
            # Final summary
            logger.info("\n📊 Demo Complete - Final Summary")
            logger.info("=" * 50)
            final_jobs = self.show_xml_contents("Final XML Contents")
            
            logger.info(f"\n✅ Demo completed successfully!")
            logger.info(f"📈 Final state: {len(final_jobs)} jobs in XML file")
            logger.info("\n🎯 What happened in this demo:")
            logger.info("   1. Started with 1 initial job")
            logger.info("   2. Added 2 new jobs (Python Developer, DevOps Engineer)")
            logger.info("   3. Added 1 more job (Frontend Developer)")
            logger.info("   4. Removed 1 job (DevOps Engineer)")
            logger.info("   5. Updated 1 job (Python Developer)")
            logger.info("   6. Each change triggered reference number regeneration")
            logger.info("   7. Simulated file upload to web server")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Demo failed with error: {str(e)}")
            return False
        
        finally:
            # Clean up demo file
            if os.path.exists(self.demo_xml_file):
                os.remove(self.demo_xml_file)
                logger.info(f"🧹 Cleaned up demo file: {self.demo_xml_file}")

def main():
    """Run the automation demo"""
    demo = AutomationDemo()
    
    logger.info("🎬 XML Automation System Demo")
    logger.info("This demo shows how the system handles job changes automatically")
    logger.info("In production, this happens every 5 minutes when monitoring Bullhorn")
    logger.info("")
    
    success = demo.run_complete_demo()
    
    if success:
        logger.info("\n🎉 Demo completed successfully!")
        logger.info("The automation system is ready for production use.")
        return 0
    else:
        logger.error("\n❌ Demo failed. Please check the logs above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())