#!/usr/bin/env python3
"""
Direct replacement of numeric country IDs with country names in XML files
"""

import re
from datetime import datetime

def fix_country_ids(xml_file):
    """Replace numeric country IDs with proper country names"""
    
    # Create backup
    backup_file = f"{xml_file}.backup_country_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Read file
    with open(xml_file, 'r') as f:
        content = f.read()
    
    # Save backup
    with open(backup_file, 'w') as f:
        f.write(content)
    print(f"Created backup: {backup_file}")
    
    # Count occurrences before replacement
    count_before = len(re.findall(r'<country>\s*1\s*</country>', content))
    print(f"Found {count_before} occurrences of numeric country ID '1'")
    
    # Replace numeric country ID 1 with United States
    # This handles various whitespace patterns
    content = re.sub(
        r'<country>\s*1\s*</country>',
        '<country> United States </country>',
        content
    )
    
    # Count occurrences after replacement
    count_after = len(re.findall(r'<country>\s*1\s*</country>', content))
    
    # Write updated content
    with open(xml_file, 'w') as f:
        f.write(content)
    
    print(f"Replaced {count_before - count_after} country IDs in {xml_file}")
    
    # Verify the update
    numeric_countries = re.findall(r'<country>\s*(\d+)\s*</country>', content)
    if numeric_countries:
        print(f"⚠️ Warning: Still found {len(numeric_countries)} numeric country IDs: {set(numeric_countries)}")
    else:
        print(f"✅ All numeric country IDs have been replaced!")
    
    return count_before - count_after

def main():
    print("=" * 60)
    print("FIXING NUMERIC COUNTRY IDS IN XML FILES")
    print("=" * 60)
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_fixed = 0
    for xml_file in xml_files:
        print(f"\nProcessing {xml_file}...")
        try:
            fixed = fix_country_ids(xml_file)
            total_fixed += fixed
        except Exception as e:
            print(f"Error processing {xml_file}: {str(e)}")
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: Fixed {total_fixed} total country IDs")
    print("=" * 60)

if __name__ == "__main__":
    main()