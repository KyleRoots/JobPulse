#!/usr/bin/env python3
"""Test script to fetch job 34249 directly from Bullhorn"""

import os
import sys
from bullhorn_service import BullhornService

# Get credentials from environment
client_id = os.environ.get('BULLHORN_CLIENT_ID')
client_secret = os.environ.get('BULLHORN_CLIENT_SECRET')
username = os.environ.get('BULLHORN_USERNAME')
password = os.environ.get('BULLHORN_PASSWORD')

print("🔍 Testing direct job fetch from Bullhorn for Job ID 34249")
print("=" * 60)

# Initialize Bullhorn service
bullhorn = BullhornService(
    client_id=client_id,
    client_secret=client_secret,
    username=username,
    password=password
)

# Authenticate
print("📡 Authenticating with Bullhorn...")
if not bullhorn.authenticate():
    print("❌ Failed to authenticate with Bullhorn")
    sys.exit(1)

print("✅ Authentication successful")
print()

# Fetch job 34249 directly
print("📥 Fetching Job 34249 directly from Bullhorn...")
job = bullhorn.get_job_by_id(34249)

if job:
    print(f"✅ Job fetched successfully!")
    print(f"   Title: {job.get('title', 'N/A')}")
    print(f"   ID: {job.get('id', 'N/A')}")
    print(f"   Last Modified: {job.get('dateLastModified', 'N/A')}")
    
    # Convert timestamp if present
    if job.get('dateLastModified'):
        from datetime import datetime
        try:
            timestamp = job['dateLastModified'] / 1000  # Convert from milliseconds
            modified_date = datetime.fromtimestamp(timestamp)
            print(f"   Modified Date: {modified_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        except:
            pass
    
    print()
    print("📋 Full job data (first 500 chars of description):")
    for key, value in job.items():
        if key == 'publicDescription' and value:
            # Truncate long descriptions
            value = str(value)[:500] + "..." if len(str(value)) > 500 else value
        print(f"   {key}: {value}")
else:
    print("❌ Failed to fetch job 34249")

print()
print("=" * 60)
print("🔍 Test complete!")