#!/usr/bin/env python
"""Test what tearsheet-related endpoints are available in Bullhorn API"""

from app import app
from bullhorn_service import BullhornService
import json

def test_tearsheet_access():
    """Test various tearsheet-related API endpoints"""
    
    with app.app_context():
        service = BullhornService()
        
        if not service.authenticate():
            print("❌ Authentication failed")
            return
        
        print("✅ Authentication successful")
        print(f"Base URL: {service.base_url}")
        
        # Test 1: Try to get entity metadata for Tearsheet
        print("\n=== Testing Tearsheet Entity Metadata ===")
        try:
            url = f"{service.base_url}meta/Tearsheet"
            params = {'BhRestToken': service.rest_token}
            response = service.session.get(url, params=params)
            print(f"Tearsheet metadata status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Tearsheet fields available: {len(data.get('fields', []))}")
                # Show key fields
                for field in data.get('fields', [])[:10]:
                    print(f"  - {field.get('name')}: {field.get('type')}")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Tearsheet metadata error: {e}")
        
        # Test 2: Try to search for tearsheets
        print("\n=== Testing Tearsheet Search ===")
        try:
            url = f"{service.base_url}search/Tearsheet"
            params = {
                'query': 'id:[1 TO 999999]',
                'fields': 'id,name,description,dateAdded',
                'count': 10,
                'BhRestToken': service.rest_token
            }
            response = service.session.get(url, params=params)
            print(f"Tearsheet search status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                tearsheets = data.get('data', [])
                print(f"Found {len(tearsheets)} tearsheets")
                for ts in tearsheets[:5]:
                    print(f"  - ID: {ts.get('id')}, Name: {ts.get('name')}")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Tearsheet search error: {e}")
        
        # Test 3: Try to get tearsheet with job associations
        print("\n=== Testing Tearsheet with Job Associations ===")
        try:
            # First get a tearsheet ID
            url = f"{service.base_url}search/Tearsheet"
            params = {
                'query': 'id:[1 TO 999999]',
                'fields': 'id,name',
                'count': 1,
                'BhRestToken': service.rest_token
            }
            response = service.session.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                tearsheets = data.get('data', [])
                if tearsheets:
                    tearsheet_id = tearsheets[0]['id']
                    print(f"Testing with tearsheet ID: {tearsheet_id}")
                    
                    # Try to get associated jobs
                    url = f"{service.base_url}entity/Tearsheet/{tearsheet_id}"
                    params = {
                        'fields': 'id,name,jobOrders(id,title,status)',
                        'BhRestToken': service.rest_token
                    }
                    response = service.session.get(url, params=params)
                    print(f"Tearsheet entity status: {response.status_code}")
                    if response.status_code == 200:
                        data = response.json()
                        job_orders = data.get('data', {}).get('jobOrders', {})
                        if isinstance(job_orders, dict):
                            jobs = job_orders.get('data', [])
                            print(f"Found {len(jobs)} associated jobs")
                            for job in jobs[:3]:
                                print(f"  - Job ID: {job.get('id')}, Title: {job.get('title')}")
                        else:
                            print(f"JobOrders format: {type(job_orders)}")
                    else:
                        print(f"Error getting tearsheet entity: {response.text}")
                else:
                    print("No tearsheets found to test with")
            else:
                print(f"Could not get tearsheet for testing: {response.text}")
        except Exception as e:
            print(f"Tearsheet job association error: {e}")
        
        # Test 4: Check what entities are available
        print("\n=== Available Entities ===")
        try:
            url = f"{service.base_url}settings"
            params = {'BhRestToken': service.rest_token}
            response = service.session.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                entities = data.get('data', {}).get('entities', [])
                print(f"Available entities: {len(entities)}")
                tearsheet_entities = [e for e in entities if 'tearsheet' in e.lower()]
                print(f"Tearsheet-related entities: {tearsheet_entities}")
            else:
                print(f"Settings error: {response.text}")
        except Exception as e:
            print(f"Settings error: {e}")

if __name__ == '__main__':
    test_tearsheet_access()