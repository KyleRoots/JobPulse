#!/usr/bin/env python3
"""
Script to populate missing fields in XML file using Bullhorn API data
Preserves CDATA formatting and reference numbers
"""

import os
import sys
from lxml import etree
from app import app
from bullhorn_service import BullhornService

def get_bullhorn_service():
    """Get configured Bullhorn service using app context"""
    from app import get_bullhorn_service as app_get_bullhorn_service
    with app.app_context():
        return app_get_bullhorn_service()

def get_city_state_mapping():
    """Common city to state/province mappings for Canadian and US cities"""
    return {
        'Montreal': 'QC',
        'Vancouver': 'BC',
        'Toronto': 'ON',
        'Ottawa': 'ON',
        'Calgary': 'AB',
        'Edmonton': 'AB',
        'Winnipeg': 'MB',
        'Regina': 'SK',
        'Halifax': 'NS',
        'Charlottetown': 'PE',
        'Fredericton': 'NB',
        'St. John\'s': 'NL',
        'Whitehorse': 'YT',
        'Yellowknife': 'NT',
        'Iqaluit': 'NU',
        'Sunnyvale': 'CA',
        'Indianapolis': 'IN'
    }

def extract_cdata_text(element):
    """Extract text from element, handling CDATA sections"""
    if element is None or element.text is None:
        return ""
    # For CDATA sections, the text is stored directly
    return element.text.strip()

def set_cdata_text(element, text):
    """Set text in element as CDATA section"""
    if element is not None:
        # Clear existing content
        element.text = None
        element.tail = element.tail  # Preserve tail
        # Add CDATA section
        element.text = etree.CDATA(f" {text} ")

def fix_xml_missing_fields_preserve_cdata(xml_file_path):
    """Main function to fix missing fields while preserving CDATA"""
    print(f"🔍 Analyzing XML file: {xml_file_path}")
    
    # Connect to Bullhorn
    bullhorn_service = get_bullhorn_service()
    if not bullhorn_service.test_connection():
        print("❌ Failed to connect to Bullhorn API")
        return False
    
    print("✅ Connected to Bullhorn API")
    
    # Parse XML file preserving CDATA
    try:
        parser = etree.XMLParser(strip_cdata=False, recover=True)
        tree = etree.parse(xml_file_path, parser)
        root = tree.getroot()
    except Exception as e:
        print(f"❌ Failed to parse XML file: {e}")
        return False
    
    # Get city-state mapping
    city_state_map = get_city_state_mapping()
    
    jobs_fixed = 0
    jobs_checked = 0
    
    # Process each job
    for job_element in root.findall('.//job'):
        jobs_checked += 1
        
        # Extract job ID and basic info
        bhatsid = job_element.find('bhatsid')
        title = job_element.find('title')
        city = job_element.find('city')
        state = job_element.find('state')
        country = job_element.find('country')
        
        if bhatsid is None or title is None:
            continue
            
        job_id = extract_cdata_text(bhatsid)
        job_title = extract_cdata_text(title)
        
        print(f"\n📋 Checking Job {job_id}: {job_title}")
        
        # Check for missing fields
        missing_fields = []
        city_text = extract_cdata_text(city)
        state_text = extract_cdata_text(state)
        country_text = extract_cdata_text(country)
        
        if not state_text and city_text:
            missing_fields.append("state")
        if not country_text:
            missing_fields.append("country")
            
        if not missing_fields:
            print(f"   ✅ All fields populated")
            continue
            
        print(f"   🔧 Missing fields: {', '.join(missing_fields)}")
        
        # Try to get data from Bullhorn first
        job_data = None
        if job_id:
            try:
                job_data = bullhorn_service.get_job_by_id(job_id)
            except Exception as e:
                print(f"   ⚠️  Could not get Bullhorn data: {e}")
        
        # Fix missing state
        if "state" in missing_fields:
            new_state = ""
            
            if job_data and job_data.get('address', {}).get('state'):
                new_state = job_data['address']['state'].strip()
                print(f"   📡 Found state from Bullhorn: {new_state}")
            elif city_text in city_state_map:
                new_state = city_state_map[city_text]
                print(f"   🗺️  Mapped {city_text} → {new_state}")
            
            if new_state and state is not None:
                set_cdata_text(state, new_state)
                print(f"   ✅ Updated state: {new_state}")
                jobs_fixed += 1
        
        # Fix missing country
        if "country" in missing_fields:
            new_country = ""
            
            if job_data and job_data.get('address', {}).get('countryName'):
                new_country = job_data['address']['countryName'].strip()
                print(f"   📡 Found country from Bullhorn: {new_country}")
            elif city_text in city_state_map:
                # Infer country based on state
                if city_state_map[city_text] in ['QC', 'BC', 'ON', 'AB', 'MB', 'SK', 'NS', 'NB', 'PE', 'NL', 'YT', 'NT', 'NU']:
                    new_country = "Canada"
                else:
                    new_country = "United States"
                print(f"   🗺️  Inferred country: {new_country}")
            
            if new_country and country is not None:
                # Pad to match existing format
                padded_country = new_country + " " * (60 - len(new_country))
                set_cdata_text(country, padded_country)
                print(f"   ✅ Updated country: {new_country}")
                if "state" not in missing_fields:  # Only count if we didn't already count for state
                    jobs_fixed += 1
    
    print(f"\n📊 Summary:")
    print(f"   Jobs checked: {jobs_checked}")
    print(f"   Jobs with fixes: {jobs_fixed}")
    
    if jobs_fixed > 0:
        # Save the updated XML
        backup_path = f"{xml_file_path}.backup_cdata_preserve"
        os.rename(xml_file_path, backup_path)
        print(f"   💾 Created backup: {backup_path}")
        
        with open(xml_file_path, 'wb') as f:
            tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        print(f"   ✅ Updated XML file saved with CDATA preserved")
        return True
    else:
        print("   ℹ️  No fixes needed")
        return False

if __name__ == "__main__":
    xml_file = "myticas-job-feed.xml"
    if len(sys.argv) > 1:
        xml_file = sys.argv[1]
    
    if not os.path.exists(xml_file):
        print(f"❌ XML file not found: {xml_file}")
        sys.exit(1)
    
    success = fix_xml_missing_fields_preserve_cdata(xml_file)
    if success:
        print(f"\n🎉 Successfully fixed missing fields in {xml_file}")
        print("   CDATA formatting preserved!")
        print("   Run upload_xml_files.py to upload the updated file")
    else:
        print(f"\n✅ No changes needed for {xml_file}")