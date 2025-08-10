#!/usr/bin/env python3
"""
Fix HTML formatting issues in XML file
- Clean up excessive whitespace in country fields
- Fix missing closing </li> tags in HTML descriptions
"""

import os
import shutil
import re
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree


def fix_html_formatting_in_xml(xml_file_path):
    """Fix HTML formatting issues in the XML file"""
    print(f"🔧 Starting HTML formatting fixes for: {xml_file_path}")
    
    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{xml_file_path}.backup_html_fix_{timestamp}"
    shutil.copy2(xml_file_path, backup_path)
    print(f"✅ Created backup: {backup_path}")
    
    # Parse XML
    parser = etree.XMLParser(strip_cdata=False, recover=True)
    tree = etree.parse(xml_file_path, parser)
    root = tree.getroot()
    
    country_fixes = 0
    html_fixes = 0
    total_jobs = 0
    
    # Find all job elements
    for job in root.xpath('.//job'):
        total_jobs += 1
        
        # Fix 1: Clean up excessive whitespace in country fields
        country_elem = job.find('.//country')
        if country_elem is not None and country_elem.text:
            current_value = country_elem.text.strip()
            if 'CDATA' in current_value:
                # Extract and clean value from CDATA wrapper
                country_value = current_value.replace('<![CDATA[', '').replace(']]>', '').strip()
                # Remove excessive whitespace
                cleaned_country = ' '.join(country_value.split())
                
                if country_value != cleaned_country:
                    country_elem.text = etree.CDATA(f" {cleaned_country} ")
                    country_fixes += 1
                    print(f"  🧹 Country field cleaned: '{country_value}' → '{cleaned_country}'")
        
        # Fix 2: Fix missing closing </li> tags in descriptions
        desc_elem = job.find('.//description')
        if desc_elem is not None and desc_elem.text:
            desc_text = desc_elem.text.strip()
            if 'CDATA' in desc_text:
                # Extract HTML content from CDATA
                html_content = desc_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                
                # Fix missing closing </li> tags
                # Look for <li>content without closing </li> followed by another <li> or </ul>
                original_html = html_content
                
                # Add missing </li> tags before new <li> tags
                html_content = re.sub(r'(&lt;li&gt;[^&]*?)(?=&lt;li&gt;)', r'\1&lt;/li&gt; ', html_content)
                # Add missing </li> tags before </ul> tags  
                html_content = re.sub(r'(&lt;li&gt;[^&]*?)(?=&lt;/ul&gt;)', r'\1&lt;/li&gt; ', html_content)
                
                # Clean up multiple spaces
                html_content = re.sub(r'\s+', ' ', html_content)
                
                if original_html != html_content:
                    desc_elem.text = etree.CDATA(f" {html_content} ")
                    html_fixes += 1
                    
                    # Get job title for logging
                    title_elem = job.find('.//title')
                    job_title = "Unknown"
                    if title_elem is not None and title_elem.text:
                        title_text = title_elem.text.strip()
                        if 'CDATA' in title_text:
                            job_title = title_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                        else:
                            job_title = title_text
                    
                    print(f"  🔧 HTML fixed for job: {job_title}")
    
    # Save corrected XML
    with open(xml_file_path, 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    print(f"\n🎉 HTML formatting fixes completed!")
    print(f"   📊 Total jobs processed: {total_jobs}")
    print(f"   🧹 Country fields cleaned: {country_fixes}")
    print(f"   🔧 HTML descriptions fixed: {html_fixes}")
    print(f"   💾 Backup saved as: {backup_path}")
    
    return (country_fixes + html_fixes) > 0


if __name__ == "__main__":
    xml_file = "myticas-job-feed.xml"
    
    if not os.path.exists(xml_file):
        print(f"❌ ERROR: XML file {xml_file} not found")
        exit(1)
    
    success = fix_html_formatting_in_xml(xml_file)
    
    if success:
        print(f"\n🚀 Ready for upload to live server!")
    else:
        print(f"\n✅ No HTML formatting fixes needed")