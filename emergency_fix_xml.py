#!/usr/bin/env python3
"""
Emergency fix: Remove duplicates and clean XML files
"""

import re
import os
import paramiko
from datetime import datetime
from lxml import etree

def clean_xml_duplicates(xml_file):
    """Remove duplicate jobs and ensure unique entries"""
    
    print(f"Processing {xml_file}...")
    
    # Read file
    with open(xml_file, 'r') as f:
        content = f.read()
    
    # Create backup
    backup_file = f"{xml_file}.backup_emergency_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(backup_file, 'w') as f:
        f.write(content)
    print(f"  Created backup: {backup_file}")
    
    # Extract all jobs with their content
    job_pattern = r'<job>(.*?)</job>'
    jobs = re.findall(job_pattern, content, re.DOTALL)
    
    print(f"  Found {len(jobs)} total jobs before deduplication")
    
    # Extract unique jobs by bhatsid
    unique_jobs = {}
    
    for job_content in jobs:
        # Extract bhatsid
        bhatsid_match = re.search(r'<bhatsid><!\[CDATA\[(.*?)\]\]></bhatsid>', job_content)
        if bhatsid_match:
            bhatsid = bhatsid_match.group(1).strip()
            
            # Keep only first occurrence of each job ID
            if bhatsid not in unique_jobs:
                unique_jobs[bhatsid] = job_content
    
    print(f"  Found {len(unique_jobs)} unique jobs after deduplication")
    
    # Rebuild XML with unique jobs only
    xml_header = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher><![CDATA[Myticas Consulting]]></publisher>
  <publisherurl><![CDATA[https://www.myticas.com]]></publisherurl>'''
    
    xml_footer = '</source>'
    
    # Construct new XML
    new_content = xml_header + '\n'
    
    for bhatsid in sorted(unique_jobs.keys()):
        new_content += f'  <job>\n'
        
        # Clean up indentation in job content
        job_lines = unique_jobs[bhatsid].strip().split('\n')
        for line in job_lines:
            if line.strip():
                new_content += f'    {line.strip()}\n'
        
        new_content += f'  </job>\n'
    
    new_content += xml_footer
    
    # Write cleaned content
    with open(xml_file, 'w') as f:
        f.write(new_content)
    
    print(f"  ✅ Cleaned {xml_file}: {len(jobs)} → {len(unique_jobs)} jobs")
    
    return len(unique_jobs)

def upload_to_sftp():
    """Upload cleaned XML files to production"""
    
    hostname = os.environ.get('SFTP_HOST')
    username = os.environ.get('SFTP_USERNAME')
    password = os.environ.get('SFTP_PASSWORD')
    
    if not all([hostname, username, password]):
        print("❌ SFTP credentials not found")
        return False
    
    try:
        # Connect to SFTP
        transport = paramiko.Transport((hostname, 2222))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        print("✅ Connected to SFTP server")
        
        # Upload both XML files
        for xml_file in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
            if os.path.exists(xml_file):
                file_size = os.path.getsize(xml_file)
                sftp.put(xml_file, xml_file)
                print(f"  ✅ Uploaded {xml_file} ({file_size:,} bytes)")
        
        # Close connection
        sftp.close()
        transport.close()
        
        return True
        
    except Exception as e:
        print(f"❌ SFTP error: {str(e)}")
        return False

def main():
    print("=" * 60)
    print("EMERGENCY XML DUPLICATE CLEANUP")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_jobs = 0
    for xml_file in xml_files:
        if os.path.exists(xml_file):
            job_count = clean_xml_duplicates(xml_file)
            total_jobs += job_count
        else:
            print(f"❌ File not found: {xml_file}")
    
    print(f"\n📊 Total unique jobs across all files: {total_jobs}")
    
    # Upload to production
    print("\nUploading cleaned files to production...")
    success = upload_to_sftp()
    
    if success:
        print("\n" + "=" * 60)
        print("✅ SUCCESS: Duplicates removed and files uploaded")
        print(f"Live feed should now show {total_jobs} unique jobs")
        print("=" * 60)
    else:
        print("\n❌ Upload failed - duplicates removed locally but not uploaded")

if __name__ == "__main__":
    main()