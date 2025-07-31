#!/usr/bin/env python3
"""
Precise AI Classification Addition
=================================
This script adds AI classifications to clean XML using precise pattern matching
"""

import re
from job_classification_service import JobClassificationService

def add_ai_classifications():
    """Add AI classifications to the clean XML file"""
    
    print("🔧 ADDING AI CLASSIFICATIONS TO CLEAN XML")
    print("=" * 50)
    
    # Read the clean XML file
    with open('myticas-job-feed.xml', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Initialize classification service
    classification_service = JobClassificationService()
    
    # Updated content
    updated_content = content
    jobs_processed = 0
    
    # Pattern to find empty AI classification fields and replace them
    empty_ai_pattern = r'(\s+)<jobfunction><!\[CDATA\[\s*\]\]></jobfunction>\s*\n(\s+)<jobindustries><!\[CDATA\[\s*\]\]></jobindustries>\s*\n(\s+)<senoritylevel><!\[CDATA\[\s*\]\]></senoritylevel>'
    
    # Find all job sections to get title and description
    job_sections = []
    
    # Split content by job tags
    parts = content.split('<job>')
    for i, part in enumerate(parts[1:], 1):  # Skip first empty part
        job_content = '<job>' + part.split('</job>')[0] + '</job>'
        
        # Extract title
        title_match = re.search(r'<title><!\[CDATA\[\s*(.*?)\s*\]\]></title>', job_content)
        description_match = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>', job_content, re.DOTALL)
        
        if title_match and description_match:
            title = title_match.group(1).strip()
            description = description_match.group(1).strip()
            job_sections.append((title, description, job_content))
    
    print(f"Found {len(job_sections)} jobs to classify")
    
    # Process each job
    for title, description, job_content in job_sections:
        print(f"Classifying: {title[:50]}...")
        
        try:
            # Get AI classifications
            classifications = classification_service.classify_job(title, description)
            
            # Get classification values
            job_function = classifications.get('job_function', '')
            job_industry = classifications.get('job_industry', '')
            seniority_level = classifications.get('seniority_level', '')
            
            # Create replacement text for this specific job
            def replace_ai_fields(match):
                indent1, indent2, indent3 = match.groups()
                
                replacement = f"""{indent1}<jobfunction><![CDATA[ {job_function} ]]></jobfunction>
{indent2}<jobindustries><![CDATA[ {job_industry} ]]></jobindustries>
{indent3}<senoritylevel><![CDATA[ {seniority_level} ]]></senoritylevel>"""
                
                return replacement
            
            # Replace empty AI fields in this job's content
            updated_job_content = re.sub(empty_ai_pattern, replace_ai_fields, job_content)
            
            # Replace the job content in the main document
            updated_content = updated_content.replace(job_content, updated_job_content)
            
            jobs_processed += 1
            print(f"  ✅ {job_function} | {job_industry} | {seniority_level}")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            continue
    
    # Write the updated content
    with open('myticas-job-feed.xml', 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    # Also create scheduled version
    with open('myticas-job-feed-scheduled.xml', 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    print("=" * 50)
    print(f"🎉 AI CLASSIFICATIONS ADDED: {jobs_processed}/{len(job_sections)} jobs")
    print("✅ Ready for SFTP upload to production")

if __name__ == "__main__":
    add_ai_classifications()