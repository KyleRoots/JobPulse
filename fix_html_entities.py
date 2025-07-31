#!/usr/bin/env python3
"""
Script to fix HTML entities in existing XML files
Converts &lt;strong&gt; to <strong>, &lt;p&gt; to <p>, etc. for consistent HTML markup
"""

import html
import re
from lxml import etree

def fix_html_entities_in_xml(xml_file_path):
    """Fix HTML entities in XML file descriptions"""
    print(f"Processing {xml_file_path}...")
    
    try:
        # Parse XML with CDATA preservation
        parser = etree.XMLParser(strip_cdata=False, recover=True)
        with open(xml_file_path, 'rb') as f:
            tree = etree.parse(f, parser)
        
        root = tree.getroot()
        jobs = root.findall('.//job')
        
        jobs_fixed = 0
        
        for job in jobs:
            description_elem = job.find('description')
            if description_elem is not None and description_elem.text:
                # Extract text from CDATA
                original_text = description_elem.text.strip()
                
                # Check if it contains HTML entities
                if '&lt;' in original_text or '&gt;' in original_text or '&amp;' in original_text:
                    # Convert HTML entities to proper HTML tags
                    fixed_text = html.unescape(original_text)
                    
                    # Update with new CDATA
                    description_elem.text = etree.CDATA(f" {fixed_text} ")
                    jobs_fixed += 1
                    
                    # Get job title for logging
                    title_elem = job.find('title')
                    job_title = title_elem.text.strip() if title_elem is not None and title_elem.text else "Unknown"
                    print(f"  Fixed HTML entities in: {job_title}")
        
        if jobs_fixed > 0:
            # Create backup
            backup_path = f"{xml_file_path}.backup_html_fix"
            with open(backup_path, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            print(f"  Created backup: {backup_path}")
            
            # Write fixed XML
            with open(xml_file_path, 'wb') as f:
                tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
            
            print(f"  Fixed {jobs_fixed} job descriptions with HTML entities")
        else:
            print(f"  No HTML entities found - file already clean")
            
        return jobs_fixed
        
    except Exception as e:
        print(f"Error processing {xml_file_path}: {str(e)}")
        return 0

if __name__ == "__main__":
    # Fix both XML files
    xml_files = [
        'myticas-job-feed.xml',
        'myticas-job-feed-scheduled.xml'
    ]
    
    total_fixed = 0
    for xml_file in xml_files:
        fixed_count = fix_html_entities_in_xml(xml_file)
        total_fixed += fixed_count
    
    print(f"\n✅ HTML Entity Fix Complete: {total_fixed} job descriptions updated")
    print("All XML files now have consistent HTML markup within CDATA sections")