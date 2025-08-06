#!/usr/bin/env python3
"""
Script to update all recruiter tags in XML files to add '1' character
Changes #LI-XX to #LI-XX1 format
"""

import re
import os
import shutil
from datetime import datetime

def update_recruiter_tags_in_file(file_path):
    """Update recruiter tags in a single XML file"""
    
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False
    
    # Create backup
    backup_path = f"{file_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(file_path, backup_path)
    print(f"Created backup: {backup_path}")
    
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Define all the tag replacements
    replacements = [
        ('#LI-AG:', '#LI-AG1:'),
        ('#LI-AM:', '#LI-AM1:'),
        ('#LI-BC:', '#LI-BC1:'),
        ('#LI-CC:', '#LI-CC1:'),
        ('#LI-DS:', '#LI-DS1:'),
        ('#LI-DSC:', '#LI-DSC1:'),
        ('#LI-MAT:', '#LI-MAT1:'),
        ('#LI-MIT:', '#LI-MIT1:'),
        ('#LI-MC:', '#LI-MC1:'),
        ('#LI-MG:', '#LI-MG1:'),
        ('#LI-RS:', '#LI-RS1:'),
        ('#LI-NT:', '#LI-NT1:'),
        ('#LI-RP:', '#LI-RP1:'),
    ]
    
    # Count replacements
    total_replacements = 0
    
    # Apply each replacement
    for old_tag, new_tag in replacements:
        count = content.count(old_tag)
        if count > 0:
            content = content.replace(old_tag, new_tag)
            total_replacements += count
            print(f"  Replaced {count} instances of {old_tag} with {new_tag}")
    
    # Write the updated content back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"Total replacements in {file_path}: {total_replacements}")
    return total_replacements > 0

def main():
    """Main function to update all XML files"""
    print("=" * 60)
    print("Updating Recruiter Tags in XML Files")
    print("Adding '1' to all #LI-XX tags to become #LI-XX1")
    print("=" * 60)
    
    # List of XML files to update
    xml_files = [
        'myticas-job-feed.xml',
        'myticas-job-feed-scheduled.xml'
    ]
    
    total_files_updated = 0
    
    for xml_file in xml_files:
        print(f"\nProcessing: {xml_file}")
        if update_recruiter_tags_in_file(xml_file):
            total_files_updated += 1
        print("-" * 40)
    
    print(f"\n✅ Update complete! {total_files_updated} files updated.")
    
    # Verify the changes
    print("\n" + "=" * 60)
    print("Verification - Sample of updated tags:")
    print("=" * 60)
    
    for xml_file in xml_files:
        if os.path.exists(xml_file):
            print(f"\nSamples from {xml_file}:")
            with open(xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # Find a few examples of the new tags
                import re
                matches = re.findall(r'#LI-[A-Z]+1:', content)
                unique_tags = list(set(matches))[:5]  # Show up to 5 unique tags
                for tag in unique_tags:
                    print(f"  Found: {tag}")

if __name__ == "__main__":
    main()