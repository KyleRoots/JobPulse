#!/usr/bin/env python3
"""
Final CDATA Fix - Ensure ALL fields use CDATA with proper HTML content
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
    
    # Parse XML preserving CDATA
    parser = etree.XMLParser(strip_cdata=False, recover=True)
    tree = etree.parse(xml_file, parser)
    root = tree.getroot()
    
    # Fields that should have CDATA
    cdata_fields = [
        'title', 'company', 'description', 'city', 'state', 
        'country', 'jobtype', 'category', 'email', 'remotetype',
        'assignedrecruiter', 'jobfunction', 'jobindustries', 'senoritylevel'
    ]
    
    jobs = root.findall('.//job')
    fixed_count = 0
    
    for job in jobs:
        job_id = job.find('bhatsid')
        job_id_text = job_id.text if job_id is not None else 'Unknown'
        
        for field_name in cdata_fields:
            field = job.find(field_name)
            if field is not None and field.text:
                current_text = field.text.strip()
                
                # Special handling for description - decode HTML entities
                if field_name == 'description' and ('&lt;' in current_text or '&gt;' in current_text):
                    # Decode HTML entities to get actual HTML
                    decoded_html = html.unescape(current_text)
                    field.text = None
                    field.append(etree.CDATA(decoded_html))
                    fixed_count += 1
                    print(f"  ✅ Fixed description for job {job_id_text}")
                
                # For other fields, ensure CDATA wrapper if not already present
                elif hasattr(field, 'text') and field.text and not field.text.startswith('<![CDATA['):
                    # Store the text value
                    text_value = field.text.strip()
                    # Clear the field and add CDATA
                    field.text = None
                    field.clear()
                    # Create new text node with CDATA
                    cdata = etree.CDATA(text_value)
                    field.text = ""
                    # Set the field text directly as CDATA doesn't get appended
                    new_field = etree.Element(field_name)
                    new_field.text = cdata
                    # Replace the old field with new one
                    parent = field.getparent()
                    parent.replace(field, new_field)
    
    # Write the fixed XML
    with open(xml_file, 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    print(f"\n✅ Fixed {fixed_count} fields with proper CDATA formatting")
    print(f"📄 Updated file: {xml_file}")
    
    return fixed_count

# Main execution
if __name__ == "__main__":
    import sys
    
    files_to_fix = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    if len(sys.argv) > 1:
        files_to_fix = sys.argv[1:]
    
    total_fixes = 0
    for xml_file in files_to_fix:
        try:
            fixes = fix_cdata_formatting(xml_file)
            total_fixes += fixes
        except Exception as e:
            print(f"❌ Error processing {xml_file}: {str(e)}")
    
    if total_fixes > 0:
        print(f"\n🎉 Total fixes applied: {total_fixes}")
        print("📤 Run upload_xml_files.py to upload the corrected files")