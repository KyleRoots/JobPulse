import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import re

def clean_and_deduplicate_xml(input_file, output_file):
    # Parse the XML file
    tree = ET.parse(input_file)
    root = tree.getroot()
    
    # Keep track of seen job IDs
    seen_job_ids = set()
    jobs_to_remove = []
    
    # Find all jobs
    all_jobs = root.findall('.//job')
    print(f"Total jobs before cleanup: {len(all_jobs)}")
    
    # Identify duplicates
    for job in all_jobs:
        bhatsid_elem = job.find('bhatsid')
        if bhatsid_elem is not None and bhatsid_elem.text:
            job_id = bhatsid_elem.text.strip()
            if job_id in seen_job_ids:
                jobs_to_remove.append(job)
            else:
                seen_job_ids.add(job_id)
    
    # Remove duplicate jobs
    for job in jobs_to_remove:
        root.remove(job)
    
    print(f"Removed {len(jobs_to_remove)} duplicate jobs")
    
    # Fix CDATA formatting for all remaining jobs
    remaining_jobs = root.findall('.//job')
    for job in remaining_jobs:
        # Process each text element that should have CDATA
        for field_name in ['title', 'company', 'date', 'referencenumber', 'bhatsid', 'url', 
                          'description', 'jobtype', 'city', 'state', 'country', 'category', 
                          'apply_email', 'remotetype', 'assignedrecruiter']:
            field = job.find(field_name)
            if field is not None and field.text:
                # Clean up the text - remove existing CDATA markers if present
                text = field.text.strip()
                if text.startswith('<![CDATA[') and text.endswith(']]>'):
                    text = text[9:-3]  # Remove CDATA markers
                
                # For description field, ensure HTML entities are converted to actual HTML
                if field_name == 'description':
                    # Convert HTML entities to actual HTML
                    text = text.replace('&lt;', '<').replace('&gt;', '>')
                    text = text.replace('&amp;', '&')
                    text = text.replace('&quot;', '"')
                    text = text.replace('&apos;', "'")
                
                # Set the text with proper CDATA
                field.text = f'<![CDATA[ {text} ]]>'
    
    print(f"Total jobs after cleanup: {len(remaining_jobs)}")
    
    # Write the cleaned XML
    # Convert to string and format properly
    xml_str = ET.tostring(root, encoding='unicode')
    
    # Format the XML nicely
    formatted_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    formatted_xml += xml_str.replace('><', '>\n<')
    
    # Fix indentation
    lines = formatted_xml.split('\n')
    formatted_lines = []
    indent_level = 0
    
    for line in lines:
        stripped = line.strip()
        if stripped:
            # Decrease indent for closing tags
            if stripped.startswith('</'):
                indent_level = max(0, indent_level - 1)
            
            # Add indented line
            formatted_lines.append('  ' * indent_level + stripped)
            
            # Increase indent for opening tags (but not self-closing)
            if stripped.startswith('<') and not stripped.startswith('</') and not stripped.endswith('/>'):
                # Check if it's not a single-line element
                if not (stripped.count('<') == 2 and stripped.count('>') == 2):
                    indent_level += 1
    
    formatted_xml = '\n'.join(formatted_lines)
    
    # Write to file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(formatted_xml)
    
    return len(remaining_jobs)

# Clean the XML file
num_jobs = clean_and_deduplicate_xml('temp_xml_to_analyze.xml', 'cleaned_xml.xml')
print(f"\nCleaned XML file created with {num_jobs} unique jobs")