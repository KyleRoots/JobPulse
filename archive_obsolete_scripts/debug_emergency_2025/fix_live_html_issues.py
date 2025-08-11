#!/usr/bin/env python3
"""
Fix specific HTML formatting issues found in live XML
"""

import os
import shutil
import re
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree
from ftp_service import FTPService


def fix_live_html_issues():
    """Fix HTML formatting issues and upload directly"""
    xml_file_path = "myticas-job-feed.xml"
    
    print(f"🔧 Fixing live HTML formatting issues...")
    
    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{xml_file_path}.backup_live_fix_{timestamp}"
    shutil.copy2(xml_file_path, backup_path)
    print(f"✅ Created backup: {backup_path}")
    
    # Parse XML
    parser = etree.XMLParser(strip_cdata=False, recover=True)
    tree = etree.parse(xml_file_path, parser)
    root = tree.getroot()
    
    fixes_made = 0
    
    # Find all job elements and fix formatting issues
    for job in root.xpath('.//job'):
        job_changed = False
        
        # Get job title for logging
        title_elem = job.find('.//title')
        job_title = "Unknown Job"
        if title_elem is not None and title_elem.text:
            title_text = title_elem.text.strip()
            if 'CDATA' in title_text:
                job_title = title_text.replace('<![CDATA[', '').replace(']]>', '').strip()
        
        # Fix country field excessive padding
        country_elem = job.find('.//country')
        if country_elem is not None and country_elem.text:
            current_text = country_elem.text
            if 'CDATA' in current_text:
                country_value = current_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                # Remove excessive whitespace but keep reasonable padding
                cleaned_country = country_value.strip()
                if len(country_value) - len(cleaned_country) > 10:  # More than 10 extra spaces
                    country_elem.text = etree.CDATA(f" {cleaned_country} ")
                    print(f"  🧹 Fixed country padding for: {job_title}")
                    job_changed = True
        
        # Fix HTML list formatting in descriptions
        desc_elem = job.find('.//description')
        if desc_elem is not None and desc_elem.text:
            desc_text = desc_elem.text
            if 'CDATA' in desc_text:
                html_content = desc_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                original_html = html_content
                
                # Fix missing closing li tags - improved regex
                # Pattern: <li>content followed by another <li> without a closing </li>
                html_content = re.sub(r'(&lt;li&gt;[^&]*?)(?=\s*&lt;li&gt;)', r'\1&lt;/li&gt;\n', html_content)
                # Pattern: <li>content followed by </ul> without closing </li>
                html_content = re.sub(r'(&lt;li&gt;[^&]*?)(?=\s*&lt;/ul&gt;)', r'\1&lt;/li&gt;\n', html_content)
                
                # Clean up excess whitespace
                html_content = re.sub(r'\s+', ' ', html_content)
                html_content = html_content.strip()
                
                if original_html != html_content:
                    desc_elem.text = etree.CDATA(f" {html_content} ")
                    print(f"  🔧 Fixed HTML formatting for: {job_title}")
                    job_changed = True
        
        if job_changed:
            fixes_made += 1
    
    if fixes_made > 0:
        # Save corrected XML
        with open(xml_file_path, 'wb') as f:
            tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        print(f"\n✅ Fixed formatting issues in {fixes_made} jobs")
        
        # Upload immediately
        print("📤 Uploading fixed XML to live server...")
        
        try:
            # Get SFTP credentials
            hostname = os.environ.get('SFTP_HOST')
            username = os.environ.get('SFTP_USERNAME')
            password = os.environ.get('SFTP_PASSWORD')
            port = 2222
            
            sftp_service = FTPService(
                hostname=str(hostname),
                username=str(username), 
                password=str(password),
                target_directory="/",
                port=port,
                use_sftp=True
            )
            
            success = sftp_service.upload_file(xml_file_path)
            if success:
                print("✅ Successfully uploaded HTML-fixed XML to live server!")
                return True
            else:
                print("❌ Upload failed")
                return False
                
        except Exception as e:
            print(f"❌ Upload error: {str(e)}")
            return False
    else:
        print("✅ No HTML formatting issues found in local XML")
        return True


if __name__ == "__main__":
    success = fix_live_html_issues()
    if success:
        print("\n🎉 HTML formatting issues resolved!")
    else:
        print("\n❌ Could not resolve all issues")