#!/usr/bin/env python3
"""
Upload corrected XML with proper job 32539 description
"""
import os
import re
import paramiko
from lxml import etree

def fix_and_upload():
    """Fix job 32539 description and upload to live server"""
    
    print("=== FIXING JOB 32539 AND UPLOADING ===")
    
    # First, ensure our local XML has the correct data
    xml_file = 'myticas-job-feed.xml'
    
    try:
        with open(xml_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse XML
        parser = etree.XMLParser(strip_cdata=False)
        tree = etree.fromstring(content.encode('utf-8'), parser)
        
        # Find job 32539
        jobs = tree.xpath('//job[bhatsid[contains(text(), "32539")]]')
        
        if not jobs:
            print("ERROR: Job 32539 not found in local XML")
            return False
        
        job = jobs[0]
        print(f"✓ Found job 32539: {job.find('title').text}")
        
        # Update description with correct Bullhorn data
        desc_elem = job.find('description')
        if desc_elem is not None:
            # Set the correct description as per user requirement
            correct_description = """Location: Remote (with occasional travel to Springfield, IL)
Contract: 12 months with possibly multiyear extension

Functional Description:
We are seeking a skilled and experienced BI BusinessObjects/SQL Developer to design, develop, and maintain business intelligence solutions using SAP BusinessObjects and SQL. This role supports Medicaid/CHIP claims and other healthcare data systems, ensuring accurate reporting and data integrity for state and federal stakeholders.

Key Responsibilities:
• Design, develop, and maintain BusinessObjects reports using Medicaid/CHIP claims and subsystem data
• Write, optimize, and maintain complex SQL scripts and queries for data marts and reporting
• Create and manage BusinessObjects Universes and metadata layers
• Develop and maintain database systems to support efficient data storage and reporting
• Manage server backups and ensure system reliability
• Collaborate with stakeholders to gather requirements and recommend technical solutions
• Participate in unit, integration, and system testing
• Provide regular project updates to customers and maintain strong communication
• Mentor junior team members and provide technical training as needed
• Stay current with emerging technologies and industry best practices
• Perform additional duties as required to support project and organizational goals

Required Qualifications:
• 5+ years of experience in SAP BusinessObjects development and reporting including report Universe designing
• 5+ years of SQL development, including 4+ years of performance tuning
• 2+ years of experience in data warehouse projects
• 2+ years of experience with Teradata (v15+), including SQL Assistant
• Strong communication skills and the ability to work directly with clients
• Bachelor's or advanced degree in IT, Computer Science, Mathematics, Statistics, or a related field
• Excellent organizational skills and ability to manage multiple priorities
• Strong team collaboration and adaptability

Preferred Qualifications:
• 2+ years of experience as Business Objects server administrator
• Familiarity with Medicaid, Medicare, or healthcare-related applications
• Experience with Tableau or BusinessObjects version upgrades
• Exposure to Agile development methodologies

This is a remote role. We are considering any candidate in the US that meets the criteria above."""
            
            desc_elem.text = f" {correct_description} "
            print("✓ Updated description with correct Bullhorn data")
            
            # Save corrected XML
            xml_str = etree.tostring(tree, encoding='unicode', pretty_print=True)
            with open(xml_file, 'w', encoding='utf-8') as f:
                f.write('<?xml version=\'1.0\' encoding=\'UTF-8\'?>\n')
                f.write(xml_str)
            
            print("✓ Local XML file updated")
        
        # Now upload via SFTP
        print("\n=== UPLOADING VIA SFTP ===")
        
        sftp_hostname = os.environ.get('SFTP_HOST')
        sftp_username = os.environ.get('SFTP_USERNAME')
        sftp_password = os.environ.get('SFTP_PASSWORD')
        
        if not all([sftp_hostname, sftp_username, sftp_password]):
            print("ERROR: Missing SFTP credentials")
            return False
        
        # Create SSH connection
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=sftp_hostname,
            port=2222,
            username=sftp_username,
            password=sftp_password,
            timeout=30
        )
        
        # Upload via SFTP
        sftp = ssh.open_sftp()
        local_size = os.path.getsize(xml_file)
        print(f"Uploading {xml_file} ({local_size} bytes)...")
        
        sftp.put(xml_file, 'myticas-job-feed.xml')
        
        # Verify upload
        remote_stat = sftp.stat('myticas-job-feed.xml')
        remote_size = remote_stat.st_size
        
        print(f"Upload complete - Remote size: {remote_size} bytes")
        
        sftp.close()
        ssh.close()
        
        if remote_size == local_size:
            print("✅ SUCCESS: Upload verified")
            return True
        else:
            print(f"⚠️ WARNING: Size mismatch - local: {local_size}, remote: {remote_size}")
            return False
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if fix_and_upload():
        print("\n🎉 Job 32539 description corrected and uploaded successfully!")
        print("Changes should appear on https://myticas.com/myticas-job-feed.xml within a few moments.")
    else:
        print("\n❌ Fix/upload failed")