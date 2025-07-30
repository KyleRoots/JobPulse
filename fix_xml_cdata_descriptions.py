#!/usr/bin/env python3
"""
Fix XML file to properly format descriptions with CDATA and convert HTML entities
"""
import html
import re
from lxml import etree

def fix_xml_descriptions(input_file, output_file):
    """Fix XML descriptions to use proper CDATA and HTML tags"""
    
    # Parse the XML file
    parser = etree.XMLParser(strip_cdata=False)
    tree = etree.parse(input_file, parser)
    root = tree.getroot()
    
    # Process each job
    jobs_fixed = 0
    for job in root.findall('.//job'):
        # Fix description field
        desc_elem = job.find('description')
        if desc_elem is not None and desc_elem.text:
            # Get the text content
            desc_text = desc_elem.text.strip()
            
            # Convert HTML entities to actual HTML
            desc_text = html.unescape(desc_text)
            
            # Set the element's text to None and create a new element with CDATA
            desc_elem.text = None
            desc_elem.tail = None
            # Remove all child elements
            for child in list(desc_elem):
                desc_elem.remove(child)
            # Set text as string (lxml will handle CDATA internally)
            desc_elem.text = desc_text
            jobs_fixed += 1
        
        # Also fix other text fields that should have CDATA
        for field_name in ['title', 'company', 'city', 'state', 'country', 
                          'jobtype', 'remotetype', 'assignedrecruiter',
                          'jobfunction', 'jobindustries', 'senoritylevel']:
            elem = job.find(field_name)
            if elem is not None and elem.text:
                text_content = elem.text.strip()
                if text_content:
                    # Set text directly
                    elem.text = text_content
    
    # Write the fixed XML
    tree.write(output_file, encoding='UTF-8', xml_declaration=True, pretty_print=True)
    
    print(f"✅ Fixed {jobs_fixed} job descriptions")
    print(f"✅ Converted HTML entities to proper HTML tags")
    print(f"✅ Added CDATA wrapping to all text fields")
    
    # Get file sizes
    import os
    input_size = os.path.getsize(input_file)
    output_size = os.path.getsize(output_file)
    print(f"📊 Input file size: {input_size:,} bytes")
    print(f"📊 Output file size: {output_size:,} bytes")

if __name__ == "__main__":
    # Fix main XML file
    print("🔧 Fixing myticas-job-feed.xml...")
    fix_xml_descriptions('myticas-job-feed.xml', 'myticas-job-feed.xml')
    
    # Fix scheduled XML file
    print("\n🔧 Fixing myticas-job-feed-scheduled.xml...")
    fix_xml_descriptions('myticas-job-feed-scheduled.xml', 'myticas-job-feed-scheduled.xml')
    
    print("\n✨ XML files fixed successfully!")