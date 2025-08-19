#!/usr/bin/env python3
"""
Apply AI classifications to all jobs in the XML file
"""

import os
import sys
import json
import time
from lxml import etree
from typing import Dict, List
import logging

# Add current directory to path
sys.path.append(os.getcwd())

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def apply_classifications():
    """Apply AI classifications to all jobs in myticas-job-feed.xml"""
    
    # Import the classification service
    from job_classification_service import JobClassificationService
    
    # Initialize the service
    classifier = JobClassificationService()
    
    if not classifier.openai_client:
        logger.error("❌ OpenAI client not initialized. Check OPENAI_API_KEY")
        return False
    
    # Load the XML file
    logger.info("📄 Loading myticas-job-feed.xml...")
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        tree = etree.parse('myticas-job-feed.xml', parser)
        root = tree.getroot()
    except Exception as e:
        logger.error(f"❌ Failed to load XML: {e}")
        return False
    
    # Get all jobs
    jobs = root.findall('.//job')
    logger.info(f"📊 Found {len(jobs)} jobs to classify")
    
    # Track progress
    classified_count = 0
    failed_count = 0
    already_classified = 0
    
    for i, job in enumerate(jobs, 1):
        try:
            # Get job details
            title_elem = job.find('title')
            desc_elem = job.find('description')
            
            if title_elem is None or desc_elem is None:
                logger.warning(f"   ⚠️ Job {i}: Missing title or description")
                failed_count += 1
                continue
            
            # Extract text (handle CDATA)
            title = title_elem.text if title_elem.text else ""
            description = desc_elem.text if desc_elem.text else ""
            
            # Check if already classified
            jobfunction = job.find('jobfunction')
            jobindustries = job.find('jobindustries')
            senioritylevel = job.find('senioritylevel')
            
            # Check if any classification exists
            has_classification = False
            if jobfunction is not None and jobfunction.text and jobfunction.text.strip():
                has_classification = True
            if jobindustries is not None and jobindustries.text and jobindustries.text.strip():
                has_classification = True
            if senioritylevel is not None and senioritylevel.text and senioritylevel.text.strip():
                has_classification = True
            
            if has_classification:
                logger.info(f"   ✅ Job {i}/{len(jobs)}: '{title[:50]}...' - Already classified")
                already_classified += 1
                continue
            
            logger.info(f"   🤖 Job {i}/{len(jobs)}: Classifying '{title[:50]}...'")
            
            # Call the classification service
            result = classifier.classify_job(title, description)
            
            if result.get('success'):
                # Update the XML elements
                if jobfunction is not None:
                    jobfunction.text = etree.CDATA(result.get('job_function', ''))
                
                if jobindustries is not None:
                    jobindustries.text = etree.CDATA(result.get('industries', ''))
                
                if senioritylevel is not None:
                    senioritylevel.text = etree.CDATA(result.get('seniority_level', ''))
                
                classified_count += 1
                logger.info(f"      ✅ Classified: {result.get('job_function', 'N/A')} | "
                          f"{result.get('industries', 'N/A')} | {result.get('seniority_level', 'N/A')}")
                
                # Rate limiting - OpenAI has limits
                time.sleep(0.5)  # 500ms delay between requests
            else:
                logger.error(f"      ❌ Failed: {result.get('error', 'Unknown error')}")
                failed_count += 1
                
        except Exception as e:
            logger.error(f"   ❌ Job {i}: Error - {str(e)}")
            failed_count += 1
    
    # Save the updated XML
    logger.info("\n💾 Saving updated XML...")
    try:
        xml_str = etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding='UTF-8')
        with open('myticas-job-feed.xml', 'wb') as f:
            f.write(xml_str)
        logger.info("✅ XML saved successfully")
    except Exception as e:
        logger.error(f"❌ Failed to save XML: {e}")
        return False
    
    # Print summary
    logger.info("\n" + "="*60)
    logger.info("📊 CLASSIFICATION SUMMARY")
    logger.info(f"   Total jobs: {len(jobs)}")
    logger.info(f"   ✅ Newly classified: {classified_count}")
    logger.info(f"   📝 Already classified: {already_classified}")
    logger.info(f"   ❌ Failed: {failed_count}")
    logger.info("="*60)
    
    return True

def verify_xml_structure():
    """Verify and fix XML structure after classification"""
    
    logger.info("\n🔍 Verifying XML structure...")
    
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        tree = etree.parse('myticas-job-feed.xml', parser)
        root = tree.getroot()
        
        # Required nodes for each job
        required_nodes = [
            'title', 'company', 'date', 'referencenumber', 'bhatsid',
            'url', 'description', 'jobtype', 'city', 'state', 'country',
            'category', 'apply_email', 'remotetype', 'assignedrecruiter',
            'jobfunction', 'jobindustries', 'senioritylevel'
        ]
        
        jobs = root.findall('.//job')
        issues_fixed = 0
        
        for i, job in enumerate(jobs, 1):
            # Check for missing nodes
            for node_name in required_nodes:
                node = job.find(node_name)
                if node is None:
                    # Create missing node
                    node = etree.SubElement(job, node_name)
                    node.text = etree.CDATA("")
                    issues_fixed += 1
                    logger.warning(f"   ⚠️ Job {i}: Added missing '{node_name}' node")
                elif node.text is None:
                    # Fix empty nodes
                    node.text = etree.CDATA("")
                    issues_fixed += 1
            
            # Verify company is always "Myticas Consulting"
            company = job.find('company')
            if company is not None and company.text != "Myticas Consulting":
                company.text = etree.CDATA("Myticas Consulting")
                issues_fixed += 1
                logger.info(f"   🔧 Job {i}: Fixed company name")
        
        if issues_fixed > 0:
            # Save the fixed XML
            xml_str = etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding='UTF-8')
            with open('myticas-job-feed.xml', 'wb') as f:
                f.write(xml_str)
            logger.info(f"✅ Fixed {issues_fixed} structural issues")
        else:
            logger.info("✅ XML structure is correct - no issues found")
        
        # Validate XML
        try:
            etree.parse('myticas-job-feed.xml')
            logger.info("✅ XML validation passed")
            return True
        except Exception as e:
            logger.error(f"❌ XML validation failed: {e}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error verifying XML: {e}")
        return False

def upload_to_server():
    """Upload the classified XML to the server"""
    
    logger.info("\n📤 Uploading to server...")
    
    try:
        from ftp_service import FTPService
        
        # Get SFTP credentials
        sftp_host = os.environ.get('SFTP_HOST')
        sftp_username = os.environ.get('SFTP_USERNAME')
        sftp_password = os.environ.get('SFTP_PASSWORD')
        
        if not all([sftp_host, sftp_username, sftp_password]):
            logger.error("❌ Missing SFTP credentials")
            return False
        
        # Create FTP service and upload
        ftp = FTPService(
            hostname=sftp_host,
            username=sftp_username,
            password=sftp_password,
            target_directory="/",
            port=2222,
            use_sftp=True
        )
        
        if ftp.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml'):
            logger.info("✅ Successfully uploaded myticas-job-feed.xml")
            logger.info("\n🌐 Test the XML at: https://myticas.com/myticas-job-feed.xml")
            return True
        else:
            logger.error("❌ Failed to upload file")
            return False
            
    except Exception as e:
        logger.error(f"❌ Upload error: {e}")
        return False

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Starting AI Classification Process")
    logger.info("="*60)
    
    # Apply classifications
    if apply_classifications():
        # Verify structure
        if verify_xml_structure():
            # Upload to server
            if upload_to_server():
                logger.info("\n✨ COMPLETE: All jobs classified and uploaded!")
            else:
                logger.error("\n❌ Upload failed")
        else:
            logger.error("\n❌ XML structure verification failed")
    else:
        logger.error("\n❌ Classification process failed")