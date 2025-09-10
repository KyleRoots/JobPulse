#!/usr/bin/env python3
"""
Test script for SimplifiedXMLGenerator
Tests tearsheet fetching and XML generation functionality
"""

import os
import sys
import logging
from datetime import datetime

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_bullhorn_connection():
    """Test basic Bullhorn authentication"""
    from bullhorn_service import BullhornService
    
    print("🔐 Testing Bullhorn authentication...")
    
    # Get credentials from environment or database
    try:
        from app import db, app, GlobalSettings
        
        with app.app_context():
            credentials = {}
            for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                try:
                    setting = GlobalSettings.query.filter_by(setting_key=key).first()
                    if setting and setting.setting_value:
                        credentials[key] = setting.setting_value.strip()
                except Exception as e:
                    print(f"⚠️ Error loading credential {key}: {str(e)}")
            
            if not all(credentials.values()):
                print("❌ Missing Bullhorn credentials")
                return False
            
            # Test authentication
            bullhorn_service = BullhornService(
                client_id=credentials.get('bullhorn_client_id'),
                client_secret=credentials.get('bullhorn_client_secret'),
                username=credentials.get('bullhorn_username'),
                password=credentials.get('bullhorn_password')
            )
            
            if bullhorn_service.authenticate():
                print("✅ Bullhorn authentication successful")
                return bullhorn_service
            else:
                print("❌ Bullhorn authentication failed")
                return False
                
    except Exception as e:
        print(f"❌ Error testing Bullhorn connection: {str(e)}")
        return False

def test_tearsheet_members(bullhorn_service):
    """Test tearsheet member fetching"""
    print("📋 Testing tearsheet member fetching...")
    
    test_tearsheets = [1256, 1264, 1499, 1556]  # Same as in simplified generator
    
    for tearsheet_id in test_tearsheets:
        try:
            members = bullhorn_service.get_tearsheet_members(tearsheet_id)
            print(f"  📄 Tearsheet {tearsheet_id}: {len(members)} members")
            
            if members:
                # Show first few member IDs
                member_ids = [m.get('id') for m in members[:3] if m.get('id')]
                print(f"    Sample IDs: {member_ids}")
            
        except Exception as e:
            print(f"  ❌ Error fetching tearsheet {tearsheet_id}: {str(e)}")
    
    return True

def test_jobs_batch(bullhorn_service):
    """Test batch job fetching"""
    print("📦 Testing batch job fetching...")
    
    # Get some job IDs from first tearsheet
    try:
        members = bullhorn_service.get_tearsheet_members(1256)
        if not members:
            print("  ❌ No members found to test with")
            return False
        
        # Take first 3 job IDs for testing
        test_ids = [m.get('id') for m in members[:3] if m.get('id')]
        
        if not test_ids:
            print("  ❌ No valid job IDs found")
            return False
        
        print(f"  🔍 Testing with job IDs: {test_ids}")
        
        jobs = bullhorn_service.get_jobs_batch(test_ids)
        print(f"  ✅ Retrieved {len(jobs)} jobs out of {len(test_ids)} requested")
        
        # Show job titles
        for job in jobs:
            job_id = job.get('id')
            title = job.get('title', 'Unknown')
            status = job.get('status', 'Unknown')
            print(f"    Job {job_id}: {title} ({status})")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing batch job fetching: {str(e)}")
        return False

def test_xml_generation():
    """Test XML generation"""
    print("🔄 Testing XML generation...")
    
    try:
        from app import db, app
        from simplified_xml_generator import SimplifiedXMLGenerator
        
        with app.app_context():
            generator = SimplifiedXMLGenerator(db=db)
            
            # Test generation
            xml_content, stats = generator.generate_fresh_xml()
            
            print(f"  ✅ Generated XML: {stats['job_count']} jobs, {stats['xml_size_bytes']} bytes")
            print(f"  📊 Stats: {stats}")
            
            # Save test XML for inspection
            with open('test_generated.xml', 'w') as f:
                f.write(xml_content)
            print("  💾 Test XML saved to test_generated.xml")
            
            return True
            
    except Exception as e:
        print(f"  ❌ Error testing XML generation: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test runner"""
    print("🚀 Starting SimplifiedXMLGenerator tests...")
    print(f"Time: {datetime.now()}")
    print("-" * 50)
    
    # Test 1: Bullhorn connection
    bullhorn_service = test_bullhorn_connection()
    if not bullhorn_service:
        print("❌ Cannot proceed without Bullhorn connection")
        return False
    
    print()
    
    # Test 2: Tearsheet members
    if not test_tearsheet_members(bullhorn_service):
        print("❌ Tearsheet member test failed")
        return False
    
    print()
    
    # Test 3: Batch job fetching
    if not test_jobs_batch(bullhorn_service):
        print("❌ Batch job fetching test failed")
        return False
    
    print()
    
    # Test 4: Full XML generation
    if not test_xml_generation():
        print("❌ XML generation test failed")
        return False
    
    print()
    print("✅ All tests passed! SimplifiedXMLGenerator is working correctly.")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)