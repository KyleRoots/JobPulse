#!/usr/bin/env python3
"""
Immediate Country Field Fix Script
Corrects country ID values to proper country names in the live XML file
"""

import os
import shutil
from datetime import datetime
try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree


def map_country_id_to_name(country_value):
    """Map Bullhorn country ID to proper country name"""
    if not country_value:
        return 'United States'
        
    country_str = str(country_value).strip()
    
    # Country ID to name mapping
    country_mapping = {
        '1': 'United States',
        '2': 'Canada', 
        '3': 'Mexico',
        '4': 'United Kingdom',
        '5': 'Germany',
        '6': 'France',
        '7': 'Australia',
        '8': 'Japan',
        '9': 'India',
        '10': 'China'
    }
    
    # If it's a numeric ID, map it to country name
    if country_str.isdigit():
        return country_mapping.get(country_str, 'United States')
    
    # If it's already a proper country name, return as-is
    return country_str


def fix_country_fields_in_xml(xml_file_path):
    """Fix all country fields in the XML file"""
    print(f"🔧 Starting immediate country field correction for: {xml_file_path}")
    
    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{xml_file_path}.backup_country_fix_{timestamp}"
    shutil.copy2(xml_file_path, backup_path)
    print(f"✅ Created backup: {backup_path}")
    
    # Parse XML
    parser = etree.XMLParser(strip_cdata=False, recover=True)
    tree = etree.parse(xml_file_path, parser)
    root = tree.getroot()
    
    fixes_made = 0
    total_jobs = 0
    
    # Find all job elements
    for job in root.xpath('.//job'):
        total_jobs += 1
        country_elem = job.find('.//country')
        
        if country_elem is not None and country_elem.text:
            # Extract current value from CDATA if present
            current_value = country_elem.text.strip()
            if 'CDATA' in current_value:
                # Extract value from CDATA wrapper
                current_value = current_value.replace('<![CDATA[', '').replace(']]>', '').strip()
            
            # Apply country mapping
            corrected_value = map_country_id_to_name(current_value)
            
            # Update if different
            if current_value != corrected_value:
                # Get job title for logging
                title_elem = job.find('.//title')
                job_title = "Unknown"
                if title_elem is not None and title_elem.text:
                    title_text = title_elem.text.strip()
                    if 'CDATA' in title_text:
                        job_title = title_text.replace('<![CDATA[', '').replace(']]>', '').strip()
                    else:
                        job_title = title_text
                
                print(f"  📝 Job '{job_title}': '{current_value}' → '{corrected_value}'")
                
                # Update with CDATA wrapper
                country_elem.text = etree.CDATA(f" {corrected_value} ")
                fixes_made += 1
    
    # Save corrected XML
    with open(xml_file_path, 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    print(f"\n🎉 Country field correction completed!")
    print(f"   📊 Total jobs processed: {total_jobs}")
    print(f"   🔧 Country fields corrected: {fixes_made}")
    print(f"   💾 Backup saved as: {backup_path}")
    
    return fixes_made > 0


if __name__ == "__main__":
    xml_file = "myticas-job-feed.xml"
    
    if not os.path.exists(xml_file):
        print(f"❌ ERROR: XML file {xml_file} not found")
        exit(1)
    
    success = fix_country_fields_in_xml(xml_file)
    
    if success:
        print(f"\n🚀 Ready for upload to live server!")
    else:
        print(f"\n✅ No country field corrections needed")