#!/usr/bin/env python3
"""
Complete CDATA formatting restoration for XML job feed files
"""
import re

def restore_complete_cdata(input_file, output_file):
    """Restore complete CDATA formatting using regex"""
    print(f"Processing {input_file}...")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fields that need CDATA
    cdata_fields = [
        'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
        'description', 'jobtype', 'city', 'state', 'country', 'category',
        'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
        'jobindustries', 'senoritylevel', 'publisher', 'publisherurl'
    ]
    
    cdata_count = 0
    
    # Process each field
    for field in cdata_fields:
        # Pattern to match field content without CDATA
        pattern = f'<{field}>(?!<!\[CDATA\[)(.*?)</{field}>'
        
        def add_cdata(match):
            nonlocal cdata_count
            content = match.group(1)
            # Skip if already has CDATA
            if '<![CDATA[' in content:
                return match.group(0)
            cdata_count += 1
            return f'<{field}><![CDATA[{content}]]></{field}>'
        
        content = re.sub(pattern, add_cdata, content, flags=re.DOTALL)
    
    # Fix any escaped CDATA markers
    content = content.replace('&lt;![CDATA[', '<![CDATA[')
    content = content.replace(']]&gt;', ']]>')
    
    # Format the XML nicely
    # Add newlines after closing tags for readability
    content = re.sub(r'><job>', '>\n  <job>', content)
    content = re.sub(r'></job>', '>\n  </job>', content)
    content = re.sub(r'><([^/])', r'>\n    <\1', content)
    
    # Write the result
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Count total CDATA sections
    total_cdata = content.count('<![CDATA[')
    
    print(f"✓ Added/fixed {cdata_count} CDATA sections")
    print(f"✓ Total CDATA sections: {total_cdata}")
    print(f"✓ Output saved to {output_file}")
    
    return total_cdata

if __name__ == "__main__":
    import shutil
    
    # Process both XML files
    for xml_file in ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']:
        output_file = xml_file.replace('.xml', '-fixed.xml')
        total = restore_complete_cdata(xml_file, output_file)
        
        # Replace original with fixed version
        shutil.move(output_file, xml_file)
        print(f"✓ Replaced {xml_file} with fully CDATA-formatted version")
        print(f"✓ File now has {total} CDATA sections\n")