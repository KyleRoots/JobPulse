#!/usr/bin/env python
"""Explore alternative approaches to track tearsheet changes"""

from app import app
from bullhorn_service import BullhornService
import json

def explore_alternatives():
    """Explore alternative methods to track tearsheet job changes"""
    
    with app.app_context():
        service = BullhornService()
        
        if not service.authenticate():
            print("❌ Authentication failed")
            return
        
        print("✅ Authentication successful")
        
        # Approach 1: Check if JobOrder has tearsheet references
        print("\n=== Checking JobOrder Entity for Tearsheet References ===")
        try:
            url = f"{service.base_url}meta/JobOrder"
            params = {'BhRestToken': service.rest_token}
            response = service.session.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                fields = data.get('fields', [])
                tearsheet_fields = [f for f in fields if 'tearsheet' in f.get('name', '').lower()]
                print(f"JobOrder tearsheet-related fields: {len(tearsheet_fields)}")
                for field in tearsheet_fields:
                    print(f"  - {field.get('name')}: {field.get('type')} - {field.get('label')}")
            else:
                print(f"Error getting JobOrder metadata: {response.text}")
        except Exception as e:
            print(f"JobOrder metadata error: {e}")
        
        # Approach 2: Check JobSubmission entity (jobs submitted to tearsheets)
        print("\n=== Checking JobSubmission Entity ===")
        try:
            url = f"{service.base_url}meta/JobSubmission"
            params = {'BhRestToken': service.rest_token}
            response = service.session.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                fields = data.get('fields', [])
                relevant_fields = [f for f in fields if any(term in f.get('name', '').lower() for term in ['tearsheet', 'job', 'status'])]
                print(f"JobSubmission relevant fields: {len(relevant_fields)}")
                for field in relevant_fields[:10]:
                    print(f"  - {field.get('name')}: {field.get('type')} - {field.get('label')}")
            else:
                print(f"Error getting JobSubmission metadata: {response.text}")
        except Exception as e:
            print(f"JobSubmission metadata error: {e}")
        
        # Approach 3: Check for custom tearsheet fields or entities
        print("\n=== Checking for Custom Tearsheet Entities ===")
        try:
            url = f"{service.base_url}settings"
            params = {'BhRestToken': service.rest_token}
            response = service.session.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                all_entities = data.get('data', {}).get('entities', [])
                print(f"Total entities available: {len(all_entities)}")
                # Look for tearsheet-related entities
                tearsheet_related = [e for e in all_entities if 'tearsheet' in e.lower()]
                if tearsheet_related:
                    print(f"Tearsheet-related entities: {tearsheet_related}")
                
                # Look for job-related entities that might connect to tearsheets
                job_related = [e for e in all_entities if 'job' in e.lower()]
                print(f"Job-related entities (first 10): {job_related[:10]}")
                
                # Look for any custom entities
                custom_entities = [e for e in all_entities if e.startswith('Custom')]
                if custom_entities:
                    print(f"Custom entities: {custom_entities[:5]}")
            else:
                print(f"Error getting settings: {response.text}")
        except Exception as e:
            print(f"Settings error: {e}")
        
        # Approach 4: Test direct tearsheet entity access via ID
        print("\n=== Testing Direct Tearsheet Entity Access ===")
        try:
            # Try to access a tearsheet by ID (if we know one exists)
            # This might work even if search doesn't work
            for test_id in [1, 100, 1000]:
                url = f"{service.base_url}entity/Tearsheet/{test_id}"
                params = {
                    'fields': 'id,name,description,jobOrders(id,title)',
                    'BhRestToken': service.rest_token
                }
                response = service.session.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Found tearsheet {test_id}: {data.get('data', {}).get('name')}")
                    job_orders = data.get('data', {}).get('jobOrders', {})
                    if isinstance(job_orders, dict):
                        jobs = job_orders.get('data', [])
                        print(f"   Associated jobs: {len(jobs)}")
                    break
                elif response.status_code == 404:
                    continue  # Try next ID
                else:
                    print(f"Error accessing tearsheet {test_id}: {response.text}")
                    break
            else:
                print("No tearsheets found with test IDs")
        except Exception as e:
            print(f"Direct tearsheet access error: {e}")

if __name__ == '__main__':
    explore_alternatives()