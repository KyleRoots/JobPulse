#!/usr/bin/env python3
"""
Fix nested CDATA sections that are causing XML parsing errors
"""
import re

def fix_nested_cdata(file_path):
    """Remove nested CDATA sections and ensure proper single CDATA wrapping"""
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print(f"Fixing nested CDATA in {file_path}...")
    
    # Pattern to match fields with nested CDATA
    def fix_cdata_field(match):
        field_name = match.group(1)
        field_content = match.group(2)
        
        # Remove all existing CDATA wrappers
        while '<![CDATA[' in field_content and ']]>' in field_content:
            field_content = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', field_content, flags=re.DOTALL)
        
        # Clean up any remaining fragments
        field_content = field_content.replace('<![CDATA[', '').replace(']]>', '')
        
        # Return with single CDATA wrapper
        return f'<{field_name}><![CDATA[{field_content.strip()}]]></{field_name}>'
    
    # Fix all fields that may have nested CDATA
    fields = ['title', 'company', 'description', 'city', 'state', 'country', 
              'jobtype', 'remotetype', 'assignedrecruiter', 'jobfunction', 
              'jobindustries', 'senoritylevel', 'referencenumber', 'bhatsid', 
              'url', 'date', 'category', 'apply_email']
    
    for field in fields:
        pattern = f'<({field})>(.*?)</{field}>'
        content = re.sub(pattern, fix_cdata_field, content, flags=re.DOTALL)
    
    # Write the fixed content
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Get file info
    import os
    file_size = os.path.getsize(file_path)
    job_count = content.count('<job>')
    
    print(f"✅ Fixed nested CDATA in {file_path}")
    print(f"📊 File size: {file_size:,} bytes")
    print(f"📊 Total jobs: {job_count}")

if __name__ == "__main__":
    # Fix both XML files
    fix_nested_cdata('myticas-job-feed.xml')
    fix_nested_cdata('myticas-job-feed-scheduled.xml')
    print("✨ All XML files fixed!")