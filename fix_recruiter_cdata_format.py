#!/usr/bin/env python3
"""
Fix assignedrecruiter tags to have proper CDATA formatting
"""

import re
import shutil
from datetime import datetime

def fix_recruiter_cdata(xml_file):
    """Fix assignedrecruiter tags to have proper CDATA formatting"""
    
    print(f"🔧 Fixing assignedrecruiter CDATA formatting in {xml_file}")
    
    # Create backup
    backup_file = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(xml_file, backup_file)
    print(f"💾 Created backup: {backup_file}")
    
    # Read the file
    with open(xml_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to find assignedrecruiter without CDATA
    pattern = r'<assignedrecruiter>\s*(#LI-[A-Z]+)\s*</assignedrecruiter>'
    
    changes_made = 0
    
    def replace_recruiter(match):
        nonlocal changes_made
        linkedin_tag = match.group(1).strip()
        changes_made += 1
        return f'<assignedrecruiter><![CDATA[{linkedin_tag}]]></assignedrecruiter>'
    
    # Apply the replacement
    content = re.sub(pattern, replace_recruiter, content)
    
    # Also fix any that have extra spaces
    pattern2 = r'<assignedrecruiter>\s*<!\[CDATA\[\s*(#LI-[A-Z]+)\s*\]\]>\s*</assignedrecruiter>'
    
    def clean_recruiter(match):
        linkedin_tag = match.group(1).strip()
        return f'<assignedrecruiter><![CDATA[{linkedin_tag}]]></assignedrecruiter>'
    
    content = re.sub(pattern2, clean_recruiter, content)
    
    # Write the updated content
    with open(xml_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Made {changes_made} CDATA formatting changes")
    
    # Verify LinkedIn tags with CDATA
    cdata_recruiters = len(re.findall(r'<assignedrecruiter><!\[CDATA\[#LI-', content))
    print(f"📊 Total assignedrecruiter tags with CDATA: {cdata_recruiters}")
    
    return changes_made

def main():
    """Process both XML files"""
    files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_changes = 0
    for xml_file in files:
        try:
            changes = fix_recruiter_cdata(xml_file)
            total_changes += changes
            print(f"✅ Successfully processed {xml_file}\n")
        except Exception as e:
            print(f"❌ Error processing {xml_file}: {str(e)}")
    
    print("📤 Files are ready for upload")
    
    return total_changes

if __name__ == "__main__":
    main()