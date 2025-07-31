#!/usr/bin/env python3
"""
Safe AI Classification Restoration
==================================
This script safely restores AI classifications using text-based replacement
to avoid XML parsing issues with CDATA sections
"""

import re
from job_classification_service import JobClassificationService

def extract_job_title(job_text):
    """Extract job title from job XML section"""
    title_match = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', job_text)
    if title_match:
        return title_match.group(1).strip()
    return ""

def extract_job_description(job_text):
    """Extract job description from job XML section"""
    desc_match = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>', job_text, re.DOTALL)
    if desc_match:
        return desc_match.group(1).strip()
    return ""

def restore_ai_classifications():
    """Restore AI classifications using safe text replacement"""
    
    print("🔧 SAFELY RESTORING AI CLASSIFICATIONS")
    print("=" * 50)
    
    # Read the current XML file
    with open('myticas-job-feed.xml', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Initialize classification service
    classification_service = JobClassificationService()
    
    # Find all job sections
    job_pattern = r'(<job>.*?</job>)'
    jobs = re.findall(job_pattern, content, re.DOTALL)
    
    print(f"Found {len(jobs)} jobs to process")
    
    updated_content = content
    jobs_processed = 0
    
    for job_text in jobs:
        # Extract title and description
        title = extract_job_title(job_text)
        description = extract_job_description(job_text)
        
        if title and description:
            print(f"Processing: {title[:50]}...")
            
            try:
                # Get AI classifications
                classifications = classification_service.classify_job(title, description)
                
                # Find the existing empty AI classification fields in this job
                job_function_pattern = r'(<jobfunction><!\[CDATA\[\s*\]\]></jobfunction>)'
                job_industry_pattern = r'(<jobindustries><!\[CDATA\[\s*\]\]></jobindustries>)'
                seniority_pattern = r'(<senoritylevel><!\[CDATA\[\s*\]\]></senoritylevel>)'
                
                # Create replacements
                new_job_function = f'<jobfunction><![CDATA[ {classifications.get("job_function", "")} ]]></jobfunction>'
                new_job_industry = f'<jobindustries><![CDATA[ {classifications.get("job_industry", "")} ]]></jobindustries>' 
                new_seniority = f'<senoritylevel><![CDATA[ {classifications.get("seniority_level", "")} ]]></senoritylevel>'
                
                # Replace in the specific job section
                updated_job = re.sub(job_function_pattern, new_job_function, job_text)
                updated_job = re.sub(job_industry_pattern, new_job_industry, updated_job)
                updated_job = re.sub(seniority_pattern, new_seniority, updated_job)
                
                # Replace this job in the main content
                updated_content = updated_content.replace(job_text, updated_job)
                
                jobs_processed += 1
                print(f"  ✅ {classifications.get('job_function')} | {classifications.get('job_industry')} | {classifications.get('seniority_level')}")
                
            except Exception as e:
                print(f"  ❌ Error: {e}")
                continue
    
    # Write the updated content
    with open('myticas-job-feed.xml', 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    # Also update scheduled file
    try:
        with open('myticas-job-feed-scheduled.xml', 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"✅ Both files updated: {jobs_processed}/{len(jobs)} jobs processed")
    except:
        print(f"✅ Main file updated: {jobs_processed}/{len(jobs)} jobs processed")

if __name__ == "__main__":
    restore_ai_classifications()