#!/usr/bin/env python3
"""
Manual fix for job 32539 corruption - use correct publicDescription field
"""
import os
import re
import paramiko
from lxml import etree

def fix_job_32539_with_proper_bullhorn_data():
    """Fix job 32539 using correct publicDescription field from Bullhorn"""
    
    print("=== MANUAL FIX FOR JOB 32539 CORRUPTION ===")
    
    # Based on user screenshots, the CORRECT Bullhorn publicDescription should be:
    correct_description = """<p><strong>Location:</strong>&nbsp;Remote (with occasional travel to Springfield, IL)</p>

<p><strong>Contract:&nbsp;</strong>12 months with possibly multiyear extension</p>

<p><strong>Functional Description:&nbsp;</strong></p>

<p>Clover Consulting (a Myticas Co.) is seeking a skilled and experienced BI BusinessObjects/SQL Developer to design, develop, and maintain business intelligence solutions using SAP BusinessObjects and SQL. This role supports Medicaid/CHIP claims and other healthcare data systems, ensuring accurate reporting and data integrity for state and federal stakeholders.</p>

<p><strong>Key Responsibilities:</strong></p>

<ul>
	<li>Design, develop, and maintain BusinessObjects reports using Medicaid/CHIP claims and subsystem data.</li>
	<li>Write, optimize, and maintain complex SQL scripts and queries for data marts and reporting.</li>
	<li>Create and manage BusinessObjects Universes and metadata layers.</li>
	<li>Develop and maintain database systems to support efficient data storage and reporting.</li>
	<li>Manage server backups and ensure system reliability.</li>
	<li>Collaborate with stakeholders to gather requirements and recommend technical solutions.</li>
	<li>Participate in unit, integration, and system testing.</li>
	<li>Provide regular project updates to customers and maintain strong communication.</li>
	<li>Mentor junior team members and provide technical training as needed.</li>
	<li>Stay current with emerging technologies and industry best practices.</li>
	<li>Perform additional duties as required to support project and organizational goals.</li>
</ul>

<p><strong>Required Qualifications:</strong></p>

<ul>
	<li>5+ years of experience in SAP BusinessObjects development and reporting including report Universe designing.</li>
	<li>5+ years of SQL development, including 4+ years of performance tuning.</li>
	<li>2+ years of experience in data warehouse projects.</li>
	<li>2+ years of experience with Teradata (v15+), including SQL Assistant.</li>
	<li>Strong communication skills and the ability to work directly with clients.</li>
	<li>Bachelor's or advanced degree in IT, Computer Science, Mathematics, Statistics, or a related field.</li>
	<li>Excellent organizational skills and ability to manage multiple priorities.</li>
	<li>Strong team collaboration and adaptability.</li>
</ul>

<p><strong>Preferred Qualifications:</strong></p>

<ul>
	<li>2+ years of experience as Business Objects server administrator</li>
	<li>Familiarity with Medicaid, Medicare, or healthcare-related applications.</li>
	<li>Experience with Tableau or BusinessObjects version upgrades.</li>
	<li>Exposure to Agile development methodologies.</li>
</ul>

<p>This is a remote role. We are considering any candidate in the US that meets the criteria above.</p>"""

    xml_file = 'myticas-job-feed.xml'
    
    try:
        # Load XML
        with open(xml_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse XML
        parser = etree.XMLParser(strip_cdata=False)
        tree = etree.fromstring(content.encode('utf-8'), parser)
        
        # Find job 32539
        jobs = tree.xpath('//job[bhatsid[contains(text(), "32539")]]')
        if not jobs:
            print("ERROR: Job 32539 not found")
            return False
        
        job = jobs[0]
        print(f"✓ Found job 32539: {job.find('title').text}")
        
        # Update description with proper HTML format
        desc_elem = job.find('description')
        if desc_elem is not None:
            desc_elem.text = f" {correct_description} "
            print("✓ Updated description with correct publicDescription from Bullhorn")
            
            # Save corrected XML
            xml_str = etree.tostring(tree, encoding='unicode', pretty_print=True)
            with open(xml_file, 'w', encoding='utf-8') as f:
                f.write('<?xml version=\'1.0\' encoding=\'UTF-8\'?>\n')
                f.write(xml_str)
            
            print("✓ Local XML file corrected")
            return True
        else:
            print("ERROR: Description element not found")
            return False
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def upload_corrected_xml():
    """Upload corrected XML to live server"""
    print("\n=== UPLOADING CORRECTED XML ===")
    
    try:
        # SFTP upload
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=os.environ.get('SFTP_HOST'),
            port=2222,
            username=os.environ.get('SFTP_USERNAME'),
            password=os.environ.get('SFTP_PASSWORD'),
            timeout=30
        )
        
        sftp = ssh.open_sftp()
        local_size = os.path.getsize('myticas-job-feed.xml')
        print(f"Uploading XML file ({local_size} bytes)...")
        
        sftp.put('myticas-job-feed.xml', 'myticas-job-feed.xml')
        
        # Verify upload
        remote_stat = sftp.stat('myticas-job-feed.xml')
        print(f"Upload complete - Remote size: {remote_stat.st_size} bytes")
        
        sftp.close()
        ssh.close()
        
        if remote_stat.st_size == local_size:
            print("✅ SUCCESS: Upload verified")
            return True
        else:
            print(f"⚠️ Size mismatch - local: {local_size}, remote: {remote_stat.st_size}")
            return False
            
    except Exception as e:
        print(f"Upload error: {e}")
        return False

if __name__ == "__main__":
    if fix_job_32539_with_proper_bullhorn_data():
        if upload_corrected_xml():
            print("\n🎉 SUCCESS: Job 32539 corrected with proper Bullhorn publicDescription!")
            print("Changes should appear on live XML within moments.")
        else:
            print("\n❌ Upload failed")
    else:
        print("\n❌ Fix failed")