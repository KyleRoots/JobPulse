#!/usr/bin/env python3
"""
Simple FTP Test - Test basic FTP connectivity and upload
"""

import os
from ftp_service import FTPService

def test_ftp_connection():
    """Test basic FTP connectivity"""
    print("🔌 Testing FTP connectivity...")
    
    try:
        ftp_service = FTPService(
            hostname=os.environ.get('SFTP_HOST'),
            username=os.environ.get('SFTP_USERNAME'), 
            password=os.environ.get('SFTP_PASSWORD')
        )
        
        # Test connection only
        print("🔐 Attempting FTP connection...")
        
        # Create a small test file
        test_file = 'ftp_test.txt'
        with open(test_file, 'w') as f:
            f.write('FTP test file\n')
        
        print("📤 Testing small file upload...")
        result = ftp_service.upload_file(test_file, test_file)
        
        if result:
            print("✅ FTP upload test successful!")
            # Clean up test file
            os.remove(test_file)
            return True
        else:
            print("❌ FTP upload test failed")
            return False
            
    except Exception as e:
        print(f"❌ FTP test error: {str(e)}")
        return False

if __name__ == "__main__":
    test_ftp_connection()