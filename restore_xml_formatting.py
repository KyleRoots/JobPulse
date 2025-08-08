#!/usr/bin/env python3
"""
Fix XML formatting: Remove job IDs from titles and restore CDATA sections
"""

import re
from datetime import datetime

def fix_xml_formatting(xml_file):
    """Fix XML: Remove job IDs and add CDATA sections"""
    
    # Create backup
    backup_file = f"{xml_file}.backup_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Read file
    with open(xml_file, 'r') as f:
        content = f.read()
    
    # Save backup
    with open(backup_file, 'w') as f:
        f.write(content)
    print(f"Created backup: {backup_file}")
    
    # Step 1: Remove job IDs from titles
    # Pattern: <title> Some Title (12345) </title>
    def clean_title(match):
        full_tag = match.group(0)
        content_part = match.group(1)
        # Remove job ID in parentheses
        cleaned = re.sub(r'\s*\(\d{5}\)\s*', '', content_part).strip()
        return f'<title><![CDATA[ {cleaned} ]]></title>'
    
    # Clean titles and add CDATA
    content = re.sub(r'<title>(.*?)</title>', clean_title, content, flags=re.DOTALL)
    
    # Step 2: Add CDATA to other text fields if not already present
    fields_to_wrap = ['company', 'date', 'referencenumber', 'bhatsid', 'url', 
                      'description', 'jobtype', 'city', 'state', 'country', 
                      'category', 'apply_email', 'remotetype', 'assignedrecruiter',
                      'jobfunction', 'jobindustries', 'senoritylevel']
    
    for field in fields_to_wrap:
        # Skip if already has CDATA
        if f'<{field}><![CDATA[' not in content:
            # Pattern: <field>content</field>
            def add_cdata(match):
                full_tag = match.group(0)
                content_part = match.group(1)
                # Don't double-wrap if already has CDATA
                if '<![CDATA[' in content_part:
                    return full_tag
                # Add CDATA wrapper
                return f'<{field}><![CDATA[{content_part}]]></{field}>'
            
            pattern = f'<{field}>(.*?)</{field}>'
            content = re.sub(pattern, add_cdata, content, flags=re.DOTALL)
    
    # Write fixed content
    with open(xml_file, 'w') as f:
        f.write(content)
    
    print(f"✅ Fixed XML formatting for {xml_file}")
    
    # Verify the fix
    # Count titles with IDs
    titles_with_ids = re.findall(r'<title>.*?\(\d{5}\).*?</title>', content)
    print(f"  Titles with job IDs remaining: {len(titles_with_ids)}")
    
    # Check CDATA sections
    cdata_count = content.count('<![CDATA[')
    print(f"  CDATA sections: {cdata_count}")
    
    # Show sample titles
    titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', content)[:3]
    if titles:
        print(f"  Sample titles:")
        for i, title in enumerate(titles, 1):
            print(f"    {i}. {title.strip()}")
    
    return True

def main():
    print("=" * 60)
    print("RESTORING XML FORMATTING")
    print("=" * 60)
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    for xml_file in xml_files:
        print(f"\nProcessing {xml_file}...")
        try:
            fix_xml_formatting(xml_file)
        except Exception as e:
            print(f"Error processing {xml_file}: {str(e)}")
    
    print("\n" + "=" * 60)
    print("XML FORMATTING RESTORED")
    print("=" * 60)

if __name__ == "__main__":
    main()