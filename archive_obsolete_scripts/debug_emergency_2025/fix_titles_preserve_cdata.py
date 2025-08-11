#!/usr/bin/env python3
"""
Remove job IDs from titles while preserving CDATA sections
"""

import re
from datetime import datetime

def fix_titles_with_cdata(xml_file):
    """Remove job IDs from titles while preserving CDATA formatting"""
    
    # Create backup
    backup_file = f"{xml_file}.backup_fix_titles_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Read file
    with open(xml_file, 'r') as f:
        content = f.read()
    
    # Save backup
    with open(backup_file, 'w') as f:
        f.write(content)
    print(f"Created backup: {backup_file}")
    
    # Count titles with IDs before
    titles_with_ids = re.findall(r'<title><!\[CDATA\[.*?\(\d{5}\).*?\]\]></title>', content)
    print(f"Found {len(titles_with_ids)} titles with job IDs")
    
    # Remove job IDs from titles within CDATA sections
    # Pattern: <title><![CDATA[ Title Text (12345) ]]></title>
    # Replace with: <title><![CDATA[ Title Text ]]></title>
    
    def remove_id_from_title(match):
        """Remove job ID from title match"""
        full_title = match.group(0)
        # Extract the content between CDATA tags
        cdata_match = re.search(r'<!\[CDATA\[(.*?)\]\]>', full_title)
        if cdata_match:
            title_text = cdata_match.group(1)
            # Remove the job ID in parentheses
            cleaned_title = re.sub(r'\s*\(\d{5}\)\s*', ' ', title_text).strip()
            # Return the title with CDATA preserved
            return f'<title><![CDATA[ {cleaned_title} ]]></title>'
        return full_title
    
    # Apply the replacement
    content = re.sub(
        r'<title><!\[CDATA\[.*?\]\]></title>',
        remove_id_from_title,
        content
    )
    
    # Count titles with IDs after
    titles_with_ids_after = re.findall(r'<title><!\[CDATA\[.*?\(\d{5}\).*?\]\]></title>', content)
    print(f"Titles with job IDs after cleaning: {len(titles_with_ids_after)}")
    
    # Write updated content
    with open(xml_file, 'w') as f:
        f.write(content)
    
    print(f"✅ Successfully cleaned {len(titles_with_ids) - len(titles_with_ids_after)} job titles")
    
    # Show a few examples
    cleaned_titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', content)[:5]
    print("\nFirst 5 cleaned titles:")
    for i, title in enumerate(cleaned_titles, 1):
        print(f"  {i}. {title.strip()}")
    
    return len(titles_with_ids) - len(titles_with_ids_after)

def main():
    print("=" * 60)
    print("FIXING JOB TITLES WHILE PRESERVING CDATA")
    print("=" * 60)
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_fixed = 0
    for xml_file in xml_files:
        print(f"\nProcessing {xml_file}...")
        try:
            fixed = fix_titles_with_cdata(xml_file)
            total_fixed += fixed
        except Exception as e:
            print(f"Error processing {xml_file}: {str(e)}")
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: Fixed {total_fixed} total job titles")
    print("=" * 60)

if __name__ == "__main__":
    main()