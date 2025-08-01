#!/usr/bin/env python3
"""
Proper CDATA Fix - Ensure ALL fields use CDATA with actual HTML content
"""

import html
import re
from lxml import etree
import shutil
from datetime import datetime

def fix_cdata_formatting(xml_file):
    """Fix CDATA formatting for all text fields, especially description"""
    
    print(f"🔧 Fixing CDATA formatting in {xml_file}")
    
    # Create backup
    backup_file = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(xml_file, backup_file)
    print(f"💾 Created backup: {backup_file}")
    
    # Read the file content
    with open(xml_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fix description fields - convert HTML entities to actual HTML within CDATA
    # Pattern to find description fields with HTML entities
    desc_pattern = r'<description>\s*(&lt;.*?&gt;.*?)\s*</description>'
    
    def replace_description(match):
        encoded_content = match.group(1)
        # Decode HTML entities
        decoded_html = html.unescape(encoded_content)
        # Return with CDATA wrapper
        return f'<description><![CDATA[{decoded_html}]]></description>'
    
    # Replace all description fields
    content = re.sub(desc_pattern, replace_description, content, flags=re.DOTALL)
    
    # Also fix other text fields that should have CDATA
    text_fields = ['title', 'company', 'city', 'state', 'country', 'jobtype', 
                   'category', 'email', 'remotetype', 'assignedrecruiter']
    
    for field in text_fields:
        # Pattern to find fields without CDATA
        pattern = f'<{field}>\\s*([^<]+?)\\s*</{field}>'
        
        def replace_field(match):
            content_text = match.group(1).strip()
            if not content_text.startswith('<![CDATA['):
                return f'<{field}><![CDATA[{content_text}]]></{field}>'
            return match.group(0)
        
        content = re.sub(pattern, replace_field, content)
    
    # Write the fixed content
    with open(xml_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Verify the fix by counting CDATA sections
    cdata_count = content.count('<![CDATA[')
    print(f"✅ Added/verified {cdata_count} CDATA sections")
    print(f"📄 Updated file: {xml_file}")
    
    return cdata_count

# Main execution
if __name__ == "__main__":
    import sys
    
    files_to_fix = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    if len(sys.argv) > 1:
        files_to_fix = sys.argv[1:]
    
    total_cdata = 0
    for xml_file in files_to_fix:
        try:
            cdata_count = fix_cdata_formatting(xml_file)
            total_cdata += cdata_count
            print(f"✅ Successfully processed {xml_file}")
        except Exception as e:
            print(f"❌ Error processing {xml_file}: {str(e)}")
            import traceback
            traceback.print_exc()
    
    if total_cdata > 0:
        print(f"\n🎉 Total CDATA sections: {total_cdata}")
        print("📤 Run upload_xml_files.py to upload the corrected files")