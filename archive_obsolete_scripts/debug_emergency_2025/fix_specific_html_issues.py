#!/usr/bin/env python3
"""
Fix specific HTML formatting issues found in live XML:
1. Missing closing </li> tags
2. Excessive country field padding
"""

import os
import re
from datetime import datetime
from xml.dom import minidom

def fix_xml_formatting_issues():
    """Fix specific formatting issues in XML file"""
    xml_file = "myticas-job-feed.xml"
    
    print("🔧 Fixing specific HTML formatting issues...")
    
    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{xml_file}.backup_specific_fix_{timestamp}"
    os.system(f"cp '{xml_file}' '{backup_path}'")
    print(f"✅ Backup created: {backup_path}")
    
    # Read XML file
    with open(xml_file, 'r', encoding='utf-8') as f:
        xml_content = f.read()
    
    fixes_made = 0
    original_content = xml_content
    
    # Fix 1: Remove excessive whitespace in country fields
    # Pattern: <country><![CDATA[ Country_name + lots of spaces ]]></country>
    def fix_country_padding(match):
        country_name = match.group(1).strip()
        return f"<country><![CDATA[ {country_name} ]]></country>"
    
    country_pattern = r'<country><!\[CDATA\[\s*([^]]+?)\s*\]\]></country>'
    before_country_fix = xml_content
    xml_content = re.sub(country_pattern, fix_country_padding, xml_content)
    
    if xml_content != before_country_fix:
        print("  🧹 Fixed excessive country field padding")
        fixes_made += 1
    
    # Fix 2: Add missing closing </li> tags
    # Pattern: <li>content followed by another <li> or </ul> without closing </li>
    
    # First fix: <li>content<li> (missing </li> before next <li>)
    before_li_fix = xml_content
    xml_content = re.sub(r'(&lt;li&gt;[^&]*?)(\s*&lt;li&gt;)', r'\1&lt;/li&gt;\n\2', xml_content)
    
    # Second fix: <li>content</ul> (missing </li> before </ul>)
    xml_content = re.sub(r'(&lt;li&gt;[^&]*?)(\s*&lt;/ul&gt;)', r'\1&lt;/li&gt;\n\2', xml_content)
    
    if xml_content != before_li_fix:
        print("  🔧 Added missing closing </li> tags")
        fixes_made += 1
    
    # Fix 3: Clean up excessive whitespace in descriptions
    # Remove multiple spaces and normalize spacing
    def clean_description_spacing(match):
        desc_content = match.group(1)
        # Replace multiple whitespace with single space
        cleaned = re.sub(r'\s+', ' ', desc_content)
        return f"<description><![CDATA[{cleaned}]]></description>"
    
    desc_pattern = r'<description><!\[CDATA\[(.*?)\]\]></description>'
    before_spacing_fix = xml_content
    xml_content = re.sub(desc_pattern, clean_description_spacing, xml_content, flags=re.DOTALL)
    
    if xml_content != before_spacing_fix:
        print("  📝 Cleaned up description spacing")
        fixes_made += 1
    
    # Write corrected XML
    with open(xml_file, 'w', encoding='utf-8') as f:
        f.write(xml_content)
    
    print(f"\n📊 Summary:")
    print(f"   ✅ Total fixes applied: {fixes_made}")
    print(f"   💾 Backup saved: {backup_path}")
    
    return fixes_made > 0

def upload_fixed_xml():
    """Upload the fixed XML to live server"""
    try:
        from ftp_service import FTPService
        
        hostname = os.environ.get('SFTP_HOST')
        username = os.environ.get('SFTP_USERNAME')
        password = os.environ.get('SFTP_PASSWORD')
        port = 2222
        
        if not all([hostname, username, password]):
            print("❌ SFTP credentials missing")
            return False
        
        sftp_service = FTPService(
            hostname=str(hostname),
            username=str(username),
            password=str(password),
            target_directory="/",
            port=port,
            use_sftp=True
        )
        
        print("📤 Uploading fixed XML to live server...")
        success = sftp_service.upload_file("myticas-job-feed.xml")
        
        if success:
            print("✅ Successfully uploaded HTML-fixed XML!")
            return True
        else:
            print("❌ Upload failed")
            return False
            
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return False

if __name__ == "__main__":
    # Fix formatting issues
    fixed = fix_xml_formatting_issues()
    
    if fixed:
        # Upload to live server
        upload_success = upload_fixed_xml()
        
        if upload_success:
            print("\n🎉 HTML formatting issues fixed and deployed!")
        else:
            print("\n❌ Fixes applied but upload failed")
    else:
        print("\n✅ No formatting issues found to fix")