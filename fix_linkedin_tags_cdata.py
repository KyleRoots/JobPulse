#!/usr/bin/env python3
"""
Fix LinkedIn tags and ensure CDATA formatting
This script converts recruiter names to LinkedIn tags while maintaining CDATA
"""

import re
import shutil
from datetime import datetime

# LinkedIn tag mapping
LINKEDIN_TAGS = {
    'Robert Pittore': '#LI-RP',
    'Michael Theodossiou': '#LI-MIT',
    'myticas': '#LI-MYT',
    'Hetal Thakur': '#LI-HT',
    'Karen Hill': '#LI-KH',
    'Matheo Theodossiou': '#LI-MAT',
    'Nick Theodossiou': '#LI-NT',
    'Alyssa Crosse': '#LI-AC',
    'Keith Roots': '#LI-KR',
    'Margarita Theodossiou': '#LI-MT',
    'Karina Keuylian': '#LI-KK',
    'Kristina Lobo': '#LI-KL',
    'Kelly Thompson': '#LI-KT',
    'Ricardo Nunez': '#LI-RN',
    'Jose Sandoval': '#LI-JS'
}

def fix_linkedin_tags_and_cdata(xml_file):
    """Fix LinkedIn tags and ensure CDATA formatting"""
    
    print(f"🔧 Fixing LinkedIn tags and CDATA in {xml_file}")
    
    # Create backup
    backup_file = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(xml_file, backup_file)
    print(f"💾 Created backup: {backup_file}")
    
    # Read the file
    with open(xml_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fix assignedrecruiter tags
    changes_made = 0
    
    # Pattern to find assignedrecruiter fields
    pattern = r'<assignedrecruiter>(?:<!\[CDATA\[)?\s*([^<]+?)\s*(?:\]\]>)?</assignedrecruiter>'
    
    def replace_recruiter(match):
        nonlocal changes_made
        recruiter_text = match.group(1).strip()
        
        # Remove CDATA wrapper if present
        if recruiter_text.startswith('<![CDATA['):
            recruiter_text = recruiter_text[9:]
        if recruiter_text.endswith(']]>'):
            recruiter_text = recruiter_text[:-3]
        
        recruiter_text = recruiter_text.strip()
        
        # Check if it's already a LinkedIn tag
        if recruiter_text.startswith('#LI-'):
            return f'<assignedrecruiter><![CDATA[{recruiter_text}]]></assignedrecruiter>'
        
        # Convert name to LinkedIn tag
        linkedin_tag = LINKEDIN_TAGS.get(recruiter_text, recruiter_text)
        
        if linkedin_tag != recruiter_text:
            changes_made += 1
            print(f"  ✅ Converted: {recruiter_text} → {linkedin_tag}")
        
        return f'<assignedrecruiter><![CDATA[{linkedin_tag}]]></assignedrecruiter>'
    
    # Apply the replacement
    content = re.sub(pattern, replace_recruiter, content)
    
    # Write the updated content
    with open(xml_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Made {changes_made} LinkedIn tag conversions")
    
    # Count LinkedIn tags
    linkedin_count = content.count('#LI-')
    print(f"📊 Total LinkedIn tags: {linkedin_count}")
    
    return changes_made

def main():
    """Process both XML files"""
    files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_changes = 0
    for xml_file in files:
        try:
            changes = fix_linkedin_tags_and_cdata(xml_file)
            total_changes += changes
            print(f"✅ Successfully processed {xml_file}\n")
        except Exception as e:
            print(f"❌ Error processing {xml_file}: {str(e)}")
    
    if total_changes > 0:
        print(f"\n🎉 Total LinkedIn tag conversions: {total_changes}")
    
    print("📤 Files are ready for upload")
    
    return total_changes

if __name__ == "__main__":
    main()