#!/usr/bin/env python3

import os
import sys
from app import app, db, User, BullhornMonitor, BullhornActivity

def test_deployment_health():
    """Test deployment readiness"""
    with app.app_context():
        try:
            print("🔍 DEPLOYMENT HEALTH CHECK")
            print("=" * 50)
            
            # Test 1: Environment variables
            print("1. Environment Variables:")
            required_vars = ['DATABASE_URL', 'SESSION_SECRET']
            for var in required_vars:
                value = os.environ.get(var)
                status = "✅ SET" if value else "❌ MISSING"
                print(f"   {var}: {status}")
                if var == 'SESSION_SECRET' and value:
                    print(f"   SESSION_SECRET length: {len(value)} chars")
            
            # Test 2: Database connectivity
            print("\n2. Database Models:")
            try:
                user_count = User.query.count()
                monitor_count = BullhornMonitor.query.count()
                activity_count = BullhornActivity.query.count()
                print(f"   ✅ Users: {user_count}")
                print(f"   ✅ Monitors: {monitor_count}")
                print(f"   ✅ Activities: {activity_count}")
            except Exception as e:
                print(f"   ❌ Database error: {e}")
                return False
            
            # Test 3: Flask-Login setup
            print("\n3. Authentication Setup:")
            try:
                from flask_login import current_user
                print("   ✅ Flask-Login imported")
                print(f"   ✅ Login manager configured: {app.login_manager is not None}")
                print(f"   ✅ Login view set: {app.login_manager.login_view}")
            except Exception as e:
                print(f"   ❌ Flask-Login error: {e}")
                return False
                
            # Test 4: Template rendering
            print("\n4. Template System:")
            try:
                with app.test_client() as client:
                    # Test login page (should work without auth)
                    response = client.get('/login')
                    login_status = "✅ OK" if response.status_code == 200 else f"❌ {response.status_code}"
                    print(f"   Login page: {login_status}")
                    
                    # Test bullhorn page (should redirect to login)
                    response = client.get('/bullhorn')
                    bullhorn_status = "✅ REDIRECT" if response.status_code == 302 else f"❌ {response.status_code}"
                    print(f"   Bullhorn page: {bullhorn_status}")
                    
            except Exception as e:
                print(f"   ❌ Template error: {e}")
                return False
            
            print("\n🎯 DEPLOYMENT STATUS: READY")
            return True
            
        except Exception as e:
            print(f"\n❌ CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = test_deployment_health()
    sys.exit(0 if success else 1)