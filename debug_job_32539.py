#!/usr/bin/env python3
"""
Debug script to check job 32539 field synchronization
"""
import os
import sys
import json
from xml_integration_service import XMLIntegrationService
from bullhorn_service import BullhornService

def main():
    print("=== DEBUG JOB 32539 FIELD SYNCHRONIZATION ===")
    
    try:
        # Initialize services with environment credentials
        bullhorn = BullhornService(
            client_id=os.environ.get('BULLHORN_CLIENT_ID'),
            client_secret=os.environ.get('BULLHORN_CLIENT_SECRET'),
            username=os.environ.get('BULLHORN_USERNAME'),
            password=os.environ.get('BULLHORN_PASSWORD')
        )
        
        print("1. Authenticating with Bullhorn...")
        if not bullhorn.authenticate():
            print("ERROR: Failed to authenticate with Bullhorn")
            return
        
        print("2. Fetching job 32539 from Bullhorn...")
        
        # Use search to find job 32539
        search_url = f"{bullhorn.base_url}/search/JobOrder"
        params = {
            'query': 'id:32539',
            'fields': 'id,title,publicDescription,employmentType,onSite,address(city,state,countryName),assignedUsers(firstName,lastName),dateLastModified',
            'count': 1,
            'BhRestToken': bullhorn.rest_token
        }
        
        response = bullhorn.session.get(search_url, params=params)
        print(f"Bullhorn API response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('data') and len(data['data']) > 0:
                job_data = data['data'][0]
                print(f"✓ Found job 32539: {job_data.get('title', 'No title')}")
                print(f"Description (first 200 chars): {job_data.get('publicDescription', 'No description')[:200]}...")
                
                # Now check what's in our XML file
                print("\n3. Checking current XML file...")
                xml_service = XMLIntegrationService()
                xml_jobs = xml_service._load_xml_jobs('myticas-job-feed.xml')
                
                if '32539' in xml_jobs:
                    xml_job = xml_jobs['32539']
                    xml_desc = xml_job.get('description', 'No description in XML')
                    print(f"XML description (first 200 chars): {xml_desc[:200]}...")
                    
                    # Compare
                    bullhorn_desc = job_data.get('publicDescription', '').strip()
                    xml_desc_clean = xml_desc.replace('<br>', '\n').replace('<![CDATA[', '').replace(']]>', '').strip()
                    
                    print(f"\n4. COMPARISON:")
                    print(f"Bullhorn length: {len(bullhorn_desc)}")
                    print(f"XML length: {len(xml_desc_clean)}")
                    print(f"Match: {bullhorn_desc == xml_desc_clean}")
                    
                    if bullhorn_desc != xml_desc_clean:
                        print("\n❌ MISMATCH DETECTED!")
                        print("Bullhorn starts with:")
                        print(f"'{bullhorn_desc[:100]}...'")
                        print("XML starts with:")
                        print(f"'{xml_desc_clean[:100]}...'")
                        
                        # Force update
                        print("\n5. FORCING FIELD UPDATE...")
                        mapped_job = xml_service.map_bullhorn_job_to_xml(job_data)
                        xml_service.update_job_in_xml('myticas-job-feed.xml', '32539', mapped_job)
                        print("✓ Update applied to XML file")
                    else:
                        print("✓ Fields match - no discrepancy found")
                else:
                    print("ERROR: Job 32539 not found in XML file")
            else:
                print("ERROR: Job 32539 not found in Bullhorn search results")
        else:
            print(f"ERROR: Bullhorn API request failed: {response.text}")
            
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()