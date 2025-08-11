#!/usr/bin/env python3
"""
Remove job IDs from titles in XML files
"""

import re
from datetime import datetime
from lxml import etree

def clean_job_titles(xml_file):
    """Remove job IDs in parentheses from job titles"""
    
    # Create backup
    backup_file = f"{xml_file}.backup_clean_titles_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Read and backup file
    with open(xml_file, 'r') as f:
        content = f.read()
    
    with open(backup_file, 'w') as f:
        f.write(content)
    print(f"Created backup: {backup_file}")
    
    # Parse XML
    try:
        tree = etree.parse(xml_file)
        root = tree.getroot()
        
        cleaned_count = 0
        
        # Process each job
        for job_elem in root.findall('.//job'):
            title_elem = job_elem.find('.//title')
            
            if title_elem is not None and title_elem.text:
                original_title = title_elem.text.strip()
                
                # Remove job ID in parentheses at the end (e.g., "(34104)")
                # Pattern matches space + (5 digits) at the end
                cleaned_title = re.sub(r'\s*\(\d{5}\)\s*$', '', original_title)
                
                if cleaned_title != original_title:
                    title_elem.text = f" {cleaned_title} "
                    cleaned_count += 1
                    
                    if cleaned_count <= 5:  # Show first 5 examples
                        print(f"  Cleaned: '{original_title}' → '{cleaned_title}'")
        
        # Write updated XML
        tree.write(xml_file, encoding='utf-8', xml_declaration=True, pretty_print=True)
        print(f"✅ Cleaned {cleaned_count} job titles")
        
        return cleaned_count
        
    except Exception as e:
        print(f"Error processing {xml_file}: {str(e)}")
        return 0

def main():
    print("=" * 60)
    print("REMOVING JOB IDS FROM TITLES IN XML FILES")
    print("=" * 60)
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    total_cleaned = 0
    for xml_file in xml_files:
        print(f"\nProcessing {xml_file}...")
        try:
            cleaned = clean_job_titles(xml_file)
            total_cleaned += cleaned
        except Exception as e:
            print(f"Error processing {xml_file}: {str(e)}")
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: Cleaned {total_cleaned} total job titles")
    print("=" * 60)

if __name__ == "__main__":
    main()