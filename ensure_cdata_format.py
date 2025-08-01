#!/usr/bin/env python3
"""
Ensure CDATA formatting for all XML fields
This script ensures proper CDATA wrapping for all text content
"""

import re
import shutil
from datetime import datetime
import html

def ensure_cdata_formatting(xml_file):
    """Ensure all text fields have proper CDATA formatting"""
    
    print(f"🔧 Ensuring CDATA formatting in {xml_file}")
    
    # Create backup
    backup_file = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(xml_file, backup_file)
    print(f"💾 Created backup: {backup_file}")
    
    # Read the file
    with open(xml_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fields that need CDATA
    fields_to_wrap = [
        'title', 'company', 'description', 'city', 'state', 'country',
        'jobtype', 'category', 'email', 'remotetype', 'assignedrecruiter',
        'jobfunction', 'jobindustries', 'senoritylevel', 'apply_email',
        'date', 'referencenumber', 'bhatsid', 'url'
    ]
    
    changes_made = 0
    
    for field in fields_to_wrap:
        # Pattern to find fields that don't have CDATA
        # This matches <field>content</field> where content doesn't start with <![CDATA[
        pattern = f'<{field}>([^<].*?)</{field}>'
        
        def process_field(match):
            nonlocal changes_made
            content = match.group(1).strip()
            
            # Skip if already has CDATA
            if content.startswith('<![CDATA['):
                return match.group(0)
            
            changes_made += 1
            
            # Special handling for description with HTML entities
            if field == 'description' and ('&lt;' in content or '&gt;' in content):
                # Unescape HTML entities for description
                content = html.unescape(content)
            
            # Wrap in CDATA
            return f'<{field}><![CDATA[{content}]]></{field}>'
        
        # Apply the replacement
        content = re.sub(pattern, process_field, content, flags=re.DOTALL)
    
    # Write the updated content
    with open(xml_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Made {changes_made} CDATA formatting changes")
    
    # Verify CDATA count
    cdata_count = content.count('<![CDATA[')
    print(f"📊 Total CDATA sections: {cdata_count}")
    
    return changes_made

def main():
    """Process both XML files"""
    files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_changes = 0
    for xml_file in files:
        try:
            changes = ensure_cdata_formatting(xml_file)
            total_changes += changes
            print(f"✅ Successfully processed {xml_file}\n")
        except Exception as e:
            print(f"❌ Error processing {xml_file}: {str(e)}")
    
    if total_changes > 0:
        print(f"\n🎉 Total changes made: {total_changes}")
        print("📤 Files are ready for upload")
    else:
        print("\n✅ All fields already have proper CDATA formatting")
    
    return total_changes

if __name__ == "__main__":
    main()