#!/usr/bin/env python3
"""
AI Classification Monitor - Standalone script to ensure all jobs in XML files have complete AI classifications
This can be run manually or scheduled to fix any missing AI classifications
"""

import os
import sys
from datetime import datetime
from lxml import etree

def check_and_fix_ai_classifications():
    """Check and fix missing AI classifications in XML files"""
    
    # Import required services
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    
    try:
        from job_classification_service import JobClassificationService
    except ImportError as e:
        print(f"Error importing JobClassificationService: {e}")
        return False
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    total_jobs_fixed = 0
    
    print(f"🔍 AI Classification Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    for xml_file in xml_files:
        if not os.path.exists(xml_file):
            print(f"⚠️  XML file not found: {xml_file}")
            continue
        
        print(f"\n📄 Checking {xml_file}...")
        
        try:
            # Parse XML file
            parser = etree.XMLParser(strip_cdata=False, recover=True)
            tree = etree.parse(xml_file, parser)
            root = tree.getroot()
            
            jobs = root.findall('.//job')
            jobs_to_fix = []
            
            # Find jobs missing AI classifications
            for job in jobs:
                job_id_elem = job.find('bhatsid')
                job_id = job_id_elem.text if job_id_elem is not None else 'Unknown'
                
                title_elem = job.find('title')
                title = title_elem.text if title_elem is not None else ''
                
                description_elem = job.find('description')
                description = description_elem.text if description_elem is not None else ''
                
                # Check if AI classifications are missing or empty
                jobfunction_elem = job.find('jobfunction')
                jobindustries_elem = job.find('jobindustries')
                senoritylevel_elem = job.find('senoritylevel')
                
                missing_ai = []
                if jobfunction_elem is None or not jobfunction_elem.text or jobfunction_elem.text.strip() == '':
                    missing_ai.append('jobfunction')
                if jobindustries_elem is None or not jobindustries_elem.text or jobindustries_elem.text.strip() == '':
                    missing_ai.append('jobindustries')
                if senoritylevel_elem is None or not senoritylevel_elem.text or senoritylevel_elem.text.strip() == '':
                    missing_ai.append('senoritylevel')
                
                if missing_ai:
                    jobs_to_fix.append({
                        'job_id': job_id,
                        'title': title,
                        'description': description,
                        'job_element': job,
                        'missing_fields': missing_ai
                    })
            
            print(f"   Total jobs: {len(jobs)}")
            print(f"   Jobs needing AI fixes: {len(jobs_to_fix)}")
            
            # Fix missing AI classifications
            if jobs_to_fix:
                classification_service = JobClassificationService()
                jobs_fixed_in_file = 0
                
                for i, job_data in enumerate(jobs_to_fix, 1):
                    try:
                        print(f"   Fixing job {i}/{len(jobs_to_fix)}: {job_data['job_id']} - {job_data['title'][:50]}...")
                        
                        # Get AI classifications for this job
                        ai_result = classification_service.classify_job(
                            job_data['title'], 
                            job_data['description']
                        )
                        
                        if ai_result and ai_result.get('success'):
                            # Update missing AI fields
                            if 'jobfunction' in job_data['missing_fields']:
                                jobfunction_elem = job_data['job_element'].find('jobfunction')
                                if jobfunction_elem is None:
                                    jobfunction_elem = etree.SubElement(job_data['job_element'], 'jobfunction')
                                    jobfunction_elem.tail = "\n    "
                                jobfunction_elem.text = etree.CDATA(f" {ai_result['job_function']} ")
                            
                            if 'jobindustries' in job_data['missing_fields']:
                                jobindustries_elem = job_data['job_element'].find('jobindustries')
                                if jobindustries_elem is None:
                                    jobindustries_elem = etree.SubElement(job_data['job_element'], 'jobindustries')
                                    jobindustries_elem.tail = "\n    "
                                jobindustries_elem.text = etree.CDATA(f" {ai_result['industries']} ")
                            
                            if 'senoritylevel' in job_data['missing_fields']:
                                senoritylevel_elem = job_data['job_element'].find('senoritylevel')
                                if senoritylevel_elem is None:
                                    senoritylevel_elem = etree.SubElement(job_data['job_element'], 'senoritylevel')
                                    senoritylevel_elem.tail = "\n  "
                                senoritylevel_elem.text = etree.CDATA(f" {ai_result['seniority_level']} ")
                            
                            jobs_fixed_in_file += 1
                            print(f"     ✅ Fixed: {ai_result['job_function']} | {ai_result['industries']} | {ai_result['seniority_level']}")
                        else:
                            error_msg = ai_result.get('error', 'Unknown error') if ai_result else 'No response'
                            print(f"     ❌ Failed to get AI classifications: {error_msg}")
                    
                    except Exception as e:
                        print(f"     ❌ Error fixing job {job_data['job_id']}: {str(e)}")
                
                # Save updated XML file
                if jobs_fixed_in_file > 0:
                    # Create backup
                    backup_path = f"{xml_file}.backup_ai_fix_{int(datetime.now().timestamp())}"
                    import shutil
                    shutil.copy2(xml_file, backup_path)
                    
                    with open(xml_file, 'wb') as f:
                        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    
                    print(f"   ✅ Updated {xml_file} with {jobs_fixed_in_file} AI classification fixes")
                    total_jobs_fixed += jobs_fixed_in_file
                else:
                    print(f"   ⚠️  No jobs were successfully fixed in {xml_file}")
            else:
                print(f"   ✅ All jobs already have complete AI classifications")
        
        except Exception as e:
            print(f"   ❌ Error processing {xml_file}: {str(e)}")
    
    print("\n" + "=" * 60)
    if total_jobs_fixed > 0:
        print(f"🎯 AI Classification Monitor Complete: Fixed {total_jobs_fixed} jobs total")
        return True
    else:
        print(f"✅ AI Classification Monitor Complete: All jobs already have complete AI classifications")
        return True

if __name__ == "__main__":
    success = check_and_fix_ai_classifications()
    sys.exit(0 if success else 1)