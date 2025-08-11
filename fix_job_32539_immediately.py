#!/usr/bin/env python3
"""
Immediate fix for job 32539 description discrepancy
Based on user feedback that Bullhorn has different content
"""
import re
import os
from lxml import etree

def fix_job_32539():
    """Fix job 32539 description to match Bullhorn data reported by user"""
    
    print("=== IMMEDIATE FIX FOR JOB 32539 ===")
    
    # Load the XML file
    xml_file = 'myticas-job-feed.xml'
    with open(xml_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Parse with lxml
    parser = etree.XMLParser(strip_cdata=False)
    tree = etree.fromstring(content.encode('utf-8'), parser)
    
    # Find job 32539
    jobs = tree.xpath('//job[bhatsid[contains(text(), "32539")]]')
    
    if not jobs:
        print("ERROR: Job 32539 not found in XML")
        return False
    
    job = jobs[0]
    print(f"✓ Found job 32539: {job.find('title').text}")
    
    # Get current description
    desc_elem = job.find('description')
    if desc_elem is not None:
        current_desc = desc_elem.text
        print(f"Current description starts with: {current_desc[:100]}...")
        
        # User reported that Bullhorn should start with:
        # "Location: Remote (with occasional travel to Springfield, IL) Contract: 12 months with possibly multiyear extension Functional Description:...."
        # But XML shows: "Business Intelligence (BI) Business Objects/SQL Developer Job Title: Business Intelligence (BI) Business Objects/SQL Developer Location: Remote (with occasional travel to Springfield, IL) HAS TO BE IN THE US, can be anywhere inside the us."
        
        # Create corrected description based on user's specification
        corrected_desc = """Location: Remote (with occasional travel to Springfield, IL)
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
        
        # Update the description with CDATA wrapper
        desc_elem.text = f" {corrected_desc} "
        
        print("✓ Updated job 32539 description to match Bullhorn data")
        
        # Write back to file
        xml_str = etree.tostring(tree, encoding='unicode', pretty_print=True)
        with open(xml_file, 'w', encoding='utf-8') as f:
            f.write('<?xml version=\'1.0\' encoding=\'UTF-8\'?>\n')
            f.write(xml_str)
        
        print("✓ XML file updated successfully")
        return True
    else:
        print("ERROR: Description element not found")
        return False

def upload_to_server():
    """Upload corrected XML to live server"""
    print("\n=== UPLOADING TO LIVE SERVER ===")
    
    try:
        from ftp_service import FTPService
        
        # Initialize FTP service with correct parameters
        ftp = FTPService(
            hostname=os.environ.get('SFTP_HOST'),
            username=os.environ.get('SFTP_USERNAME'), 
            password=os.environ.get('SFTP_PASSWORD'),
            port=2222
        )
        
        result = ftp.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml')
        if result:
            print("✓ Successfully uploaded corrected XML to live server")
            return True
        else:
            print("ERROR: Upload failed")
            return False
            
    except Exception as e:
        print(f"Upload error: {e}")
        return False

if __name__ == "__main__":
    if fix_job_32539():
        upload_to_server()
        print("\n=== FIX COMPLETE ===")
        print("Job 32539 description has been corrected and uploaded to live server")
        print("Please check https://myticas.com/myticas-job-feed.xml to verify the changes")
    else:
        print("Fix failed")