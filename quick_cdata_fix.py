#!/usr/bin/env python3
"""
Quick fix to restore CDATA formatting to XML file
"""
from lxml import etree
import xml.etree.ElementTree as ET

def quick_fix_cdata():
    print("Restoring CDATA formatting to XML file...")
    
    # Parse with ElementTree to get structure
    tree = ET.parse('myticas-job-feed.xml')
    root = tree.getroot()
    
    # Create new XML with LXML for CDATA support
    new_root = etree.Element('source')
    publisherurl = etree.SubElement(new_root, 'publisherurl')
    publisherurl.text = etree.CDATA('https://myticas.com')
    
    # Process each job
    jobs = root.findall('.//job')
    print(f"Processing {len(jobs)} jobs...")
    
    for job_elem in jobs:
        # Create new job element
        new_job = etree.SubElement(new_root, 'job')
        
        # Process each field
        for child in job_elem:
            new_elem = etree.SubElement(new_job, child.tag)
            
            # Add CDATA to all fields
            if child.text and child.text.strip():
                new_elem.text = etree.CDATA(child.text.strip())
            else:
                # Empty fields get empty CDATA
                new_elem.text = etree.CDATA('')
    
    # Save the fixed XML
    new_tree = etree.ElementTree(new_root)
    with open('myticas-job-feed.xml', 'wb') as f:
        new_tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    # Also update scheduled XML
    with open('myticas-job-feed-scheduled.xml', 'wb') as f:
        new_tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    # Verify the fix
    with open('myticas-job-feed.xml', 'r') as f:
        content = f.read()
        cdata_count = content.count('<![CDATA[')
        print(f"\n✅ CDATA formatting restored!")
        print(f"Total CDATA tags: {cdata_count}")
        print(f"File size: {len(content):,} bytes")

if __name__ == "__main__":
    quick_fix_cdata()