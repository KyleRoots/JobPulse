#!/usr/bin/env python
"""Debug Bullhorn connection issue"""

from app import app, GlobalSettings
from bullhorn_service import BullhornService
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

with app.app_context():
    print("\n=== Checking Bullhorn Credentials ===")
    
    # Check what's in the database
    settings = {}
    for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
        setting = GlobalSettings.query.filter_by(setting_key=key).first()
        if setting:
            value = setting.setting_value
            print(f"{key}: {'[PRESENT]' if value else '[EMPTY]'} - Length: {len(value) if value else 0}")
            settings[key] = value
        else:
            print(f"{key}: [NOT FOUND IN DB]")
            settings[key] = None
    
    print("\n=== Testing BullhornService ===")
    
    # Create service and check loaded credentials
    service = BullhornService()
    print(f"Service client_id: {'[PRESENT]' if service.client_id else '[MISSING]'}")
    print(f"Service username: {'[PRESENT]' if service.username else '[MISSING]'}")
    print(f"Service password: {'[PRESENT]' if service.password else '[MISSING]'}")
    print(f"Service client_secret: {'[PRESENT]' if service.client_secret else '[MISSING]'}")
    
    print("\n=== Testing Connection ===")
    result = service.test_connection()
    print(f"Final result: {result}")