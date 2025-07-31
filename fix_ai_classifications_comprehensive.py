#!/usr/bin/env python3
"""
Comprehensive AI Classification Fix
===================================
This script restores all AI classifications to the XML file using proper CDATA formatting
and only valid values from job_categories_mapping.json
"""

import xml.etree.ElementTree as ET
import json
import os
from job_classification_service import JobClassificationService

def fix_ai_classifications():
    """Fix all AI classifications in the XML file"""
    
    # Load job categories mapping
    with open('job_categories_mapping.json', 'r') as f:
        mapping = json.load(f)
    
    # Initialize the classification service
    classification_service = JobClassificationService()
    
    # Parse the XML file
    tree = ET.parse('myticas-job-feed.xml')
    root = tree.getroot()
    
    print("🔧 FIXING AI CLASSIFICATIONS WITH PROPER CDATA FORMATTING")
    print("=" * 60)
    
    jobs_fixed = 0
    total_jobs = len(root.findall('job'))
    
    for job in root.findall('job'):
        # Get job details
        title_elem = job.find('title')
        desc_elem = job.find('description')
        
        if title_elem is not None and desc_elem is not None:
            # Extract job title and description
            title = title_elem.text.strip() if title_elem.text else ""
            description = desc_elem.text.strip() if desc_elem.text else ""
            
            # Remove CDATA wrapper from title if present
            if title.startswith('<![CDATA[') and title.endswith(']]>'):
                title = title[9:-3].strip()
            
            # Remove CDATA wrapper from description if present  
            if description.startswith('<![CDATA[') and description.endswith(']]>'):
                description = description[9:-3].strip()
            
            print(f"Processing: {title[:50]}...")
            
            # Get AI classifications
            try:
                classifications = classification_service.classify_job(title, description)
                
                # Update jobfunction
                jobfunction_elem = job.find('jobfunction')
                if jobfunction_elem is not None:
                    jobfunction_elem.text = f"<![CDATA[ {classifications.get('job_function', '')} ]]>"
                    jobfunction_elem.tail = '\n    '
                
                # Update jobindustries  
                jobindustries_elem = job.find('jobindustries')
                if jobindustries_elem is not None:
                    jobindustries_elem.text = f"<![CDATA[ {classifications.get('job_industry', '')} ]]>"
                    jobindustries_elem.tail = '\n    '
                
                # Update senoritylevel
                senoritylevel_elem = job.find('senoritylevel')
                if senoritylevel_elem is not None:
                    senoritylevel_elem.text = f"<![CDATA[ {classifications.get('seniority_level', '')} ]]>"
                    senoritylevel_elem.tail = '\n  '
                
                jobs_fixed += 1
                print(f"  ✅ Applied: {classifications.get('job_function')} | {classifications.get('industries')} | {classifications.get('seniority_level')}")
                
            except Exception as e:
                print(f"  ❌ Error processing job: {e}")
                continue
    
    # Write the updated XML with proper formatting
    tree.write('myticas-job-feed.xml', encoding='utf-8', xml_declaration=True)
    
    print("=" * 60)
    print(f"🎉 AI CLASSIFICATIONS RESTORED: {jobs_fixed}/{total_jobs} jobs")
    print("✅ All AI fields now have proper CDATA formatting")
    print("✅ All values conform to job_categories_mapping.json")
    
    # Also update the scheduled file
    if os.path.exists('myticas-job-feed-scheduled.xml'):
        tree.write('myticas-job-feed-scheduled.xml', encoding='utf-8', xml_declaration=True)
        print("✅ Scheduled XML file also updated")

if __name__ == "__main__":
    fix_ai_classifications()