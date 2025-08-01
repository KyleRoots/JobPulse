#!/usr/bin/env python3
"""
Check the status of job 34085 and determine why it wasn't caught by monitoring
"""
import os
import sys
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from datetime import datetime
import json

def check_job_34085():
    """Check job 34085 status and remotetype field"""
    
    # Initialize services
    bullhorn = BullhornService()
    xml_service = XMLIntegrationService()
    
    try:
        # Authenticate with Bullhorn
        if not bullhorn.authenticate():
            print("❌ Failed to authenticate with Bullhorn")
            return
        print("✓ Authenticated with Bullhorn")
        
        if not bullhorn.base_url:
            print("❌ No base URL set after authentication")
            return
            
        # Get job details
        print("\nFetching job 34085 details...")
        # Use direct API call
        url = f"{bullhorn.base_url}entity/JobOrder/34085"
        params = {
            'fields': 'id,title,status,isOpen,isPublic,isDeleted,employmentType,address,dateAdded,dateLastModified,tearsheets,workersCompRate,owner',
            'BhRestToken': bullhorn.rest_token
        }
        response = bullhorn.session.get(url, params=params)
        
        if response.status_code != 200:
            print(f"❌ Failed to fetch job: {response.status_code} - {response.text}")
            return
            
        response_data = response.json()
        if not response_data or 'data' not in response_data:
            print("❌ Job 34085 not found in Bullhorn")
            return
        
        job = response_data['data'][0] if isinstance(response_data['data'], list) else response_data['data']
        
        print(f"\n📋 Job 34085 Details:")
        print(f"   Title: {job.get('title')}")
        print(f"   Status: {job.get('status')}")
        print(f"   isOpen: {job.get('isOpen')}")
        print(f"   isPublic: {job.get('isPublic')}")
        print(f"   isDeleted: {job.get('isDeleted')}")
        print(f"   Employment Type: {job.get('employmentType')}")
        
        # Check workersCompRate (remotetype field)
        workers_comp = job.get('workersCompRate')
        if isinstance(workers_comp, dict):
            remote_type = workers_comp.get('code', '')
            print(f"   Remote Type (workersCompRate.code): {remote_type}")
        else:
            print(f"   Remote Type (workersCompRate): {workers_comp}")
        
        # Check address for location
        address = job.get('address', {})
        if isinstance(address, dict):
            print(f"   City: {address.get('city')}")
            print(f"   State: {address.get('state')}")
            print(f"   Country: {address.get('countryCode', address.get('countryID'))}")
        
        # Check dates
        date_added = job.get('dateAdded')
        date_modified = job.get('dateLastModified')
        if date_added:
            print(f"   Date Added: {datetime.fromtimestamp(date_added/1000).strftime('%Y-%m-%d %H:%M:%S')}")
        if date_modified:
            print(f"   Date Modified: {datetime.fromtimestamp(date_modified/1000).strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Check tearsheets
        tearsheets = job.get('tearsheets', {})
        if tearsheets and 'data' in tearsheets:
            tearsheet_ids = [str(t.get('id')) for t in tearsheets['data']]
            print(f"   Tearsheets: {tearsheet_ids}")
            
            # Check if any are monitored tearsheets
            monitored_tearsheets = {
                '1258': 'Cleveland Sponsored Jobs',
                '1264': 'VMS Sponsored Jobs', 
                '1256': 'Ottawa Sponsored Jobs',
                '1499': 'Clover Sponsored Jobs',
                '1257': 'Chicago Sponsored Jobs'
            }
            
            monitored = [f"{tid} ({monitored_tearsheets[tid]})" for tid in tearsheet_ids if tid in monitored_tearsheets]
            if monitored:
                print(f"   ⚠️  In Monitored Tearsheets: {monitored}")
            else:
                print(f"   ❌ NOT in any monitored tearsheets")
        else:
            print(f"   ❌ No tearsheets associated")
        
        # Check if job should be active
        if job.get('isOpen') and not job.get('isDeleted') and job.get('status') in ['Accepting Candidates', 'Open']:
            print(f"\n✅ Job 34085 is ACTIVE and should be monitored if in a tearsheet")
        else:
            print(f"\n❌ Job 34085 is NOT ACTIVE (may be closed or deleted)")
        
        # Map to XML format to see what the remotetype would be
        print(f"\n🔄 Mapping to XML format...")
        xml_data = xml_service.map_bullhorn_job_to_xml(job)
        if xml_data:
            print(f"   XML remotetype would be: {xml_data.get('remotetype', '(empty)')}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_job_34085()