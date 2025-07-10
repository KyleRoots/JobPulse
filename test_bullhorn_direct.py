#!/usr/bin/env python
"""Direct test of Bullhorn API connection"""
import requests
import logging

logging.basicConfig(level=logging.INFO)

# Test Step 1: Get login info
username = "qts.api"
login_info_url = "https://rest.bullhornstaffing.com/rest-services/loginInfo"

print(f"Testing Bullhorn API connection for username: {username}")
print(f"Step 1: Getting login info from {login_info_url}")

try:
    response = requests.get(login_info_url, params={'username': username})
    print(f"Response status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Success! Login info received:")
        print(f"- OAuth URL: {data.get('oauthUrl', 'Not found')}")
        print(f"- REST URL: {data.get('restUrl', 'Not found')}")
        print(f"- Instance: {data.get('instanceName', 'Not found')}")
    else:
        print(f"Failed with status {response.status_code}")
        print(f"Response: {response.text}")
        
except Exception as e:
    print(f"Error: {str(e)}")
    import traceback
    traceback.print_exc()