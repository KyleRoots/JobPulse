#!/usr/bin/env python3
"""
Fix XML file to properly format descriptions with CDATA and convert HTML entities
Using a different approach that properly wraps content in CDATA
"""
import html
import re

def fix_xml_descriptions(input_file, output_file):
    """Fix XML descriptions to use proper CDATA and HTML tags"""
    
    # Read the entire file
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Function to process description content
    def fix_description(match):
        full_match = match.group(0)
        desc_content = match.group(1)
        
        # Convert HTML entities to actual HTML
        desc_content = html.unescape(desc_content)
        
        # Return with proper CDATA wrapping
        return f'<description><![CDATA[{desc_content}]]></description>'
    
    # Fix description fields with regex
    content = re.sub(
        r'<description>\s*(.*?)\s*</description>',
        fix_description,
        content,
        flags=re.DOTALL
    )
    
    # Fix other fields that need CDATA but don't have HTML entities
    fields_to_wrap = ['referencenumber', 'title', 'company', 'city', 'state', 'country',
                      'jobtype', 'remotetype', 'assignedrecruiter', 
                      'jobfunction', 'jobindustries', 'senoritylevel']
    
    for field in fields_to_wrap:
        # Check if field already has CDATA
        pattern = f'<{field}>(?!<!\\[CDATA\\[)(.*?)</{field}>'
        
        def wrap_in_cdata(match):
            content = match.group(1).strip()
            if content:
                return f'<{field}><![CDATA[{content}]]></{field}>'
            else:
                return f'<{field}></{field}>'
        
        content = re.sub(pattern, wrap_in_cdata, content, flags=re.DOTALL)
    
    # Write the fixed content
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Fixed descriptions with proper CDATA wrapping")
    print(f"✅ Converted HTML entities to proper HTML tags")
    
    # Get file sizes
    import os
    input_size = os.path.getsize(input_file)
    output_size = os.path.getsize(output_file)
    print(f"📊 Input file size: {input_size:,} bytes")
    print(f"📊 Output file size: {output_size:,} bytes")
    
    # Count jobs
    job_count = content.count('<job>')
    print(f"📊 Total jobs: {job_count}")

if __name__ == "__main__":
    # Fix main XML file
    print("🔧 Fixing myticas-job-feed.xml...")
    fix_xml_descriptions('myticas-job-feed.xml', 'myticas-job-feed.xml')
    
    # Fix scheduled XML file  
    print("\n🔧 Fixing myticas-job-feed-scheduled.xml...")
    fix_xml_descriptions('myticas-job-feed-scheduled.xml', 'myticas-job-feed-scheduled.xml')
    
    print("\n✨ XML files fixed successfully!")