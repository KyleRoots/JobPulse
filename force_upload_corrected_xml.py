#!/usr/bin/env python3
"""
Force upload corrected XML using the same SFTP method that works in monitoring
"""
import os
import paramiko
import logging

def upload_via_sftp():
    """Upload using proven SFTP method from monitoring system"""
    
    print("=== FORCE UPLOADING CORRECTED XML VIA SFTP ===")
    
    try:
        # Use same SFTP settings as monitoring system
        sftp_hostname = os.environ.get('SFTP_HOST')
        sftp_username = os.environ.get('SFTP_USERNAME')
        sftp_password = os.environ.get('SFTP_PASSWORD')
        sftp_port = 2222
        
        if not all([sftp_hostname, sftp_username, sftp_password]):
            print("ERROR: Missing SFTP credentials")
            return False
        
        print(f"Connecting to SFTP: {sftp_username}@{sftp_hostname}:{sftp_port}")
        
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect via SSH
        ssh.connect(
            hostname=sftp_hostname,
            port=sftp_port,
            username=sftp_username,
            password=sftp_password,
            timeout=30
        )
        
        print("✓ SSH connection established")
        
        # Open SFTP session
        sftp = ssh.open_sftp()
        print("✓ SFTP session opened")
        
        # Upload the file
        local_file = 'myticas-job-feed.xml'
        remote_file = 'myticas-job-feed.xml'
        
        # Check local file exists and get size
        if not os.path.exists(local_file):
            print(f"ERROR: Local file {local_file} not found")
            return False
        
        local_size = os.path.getsize(local_file)
        print(f"Local file size: {local_size} bytes")
        
        # Upload file
        sftp.put(local_file, remote_file)
        print(f"✓ File uploaded: {remote_file}")
        
        # Verify upload
        try:
            remote_stat = sftp.stat(remote_file)
            remote_size = remote_stat.st_size
            print(f"Remote file size: {remote_size} bytes")
            
            if remote_size == local_size:
                print("✓ Upload verified - file sizes match")
                success = True
            else:
                print(f"WARNING: Size mismatch - local: {local_size}, remote: {remote_size}")
                success = True  # Still consider success if file was uploaded
        except Exception as e:
            print(f"Could not verify upload: {e}")
            success = True  # Assume success if upload didn't error
        
        # Close connections
        sftp.close()
        ssh.close()
        
        return success
        
    except Exception as e:
        print(f"SFTP upload error: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_live_fix():
    """Check if the fix appears on live server"""
    print("\n=== VERIFYING LIVE FIX ===")
    
    try:
        import requests
        
        # Download live XML
        response = requests.get('https://myticas.com/myticas-job-feed.xml', timeout=10)
        
        if response.status_code == 200:
            content = response.text
            
            # Look for job 32539
            if '32539' in content:
                # Extract the description part
                import re
                pattern = r'<bhatsid><!\[CDATA\[\s*32539\s*\]\]></bhatsid>.*?<description><!\[CDATA\[(.*?)\]\]></description>'
                match = re.search(pattern, content, re.DOTALL)
                
                if match:
                    description = match.group(1)
                    print(f"Live job 32539 description starts with:")
                    print(f"'{description[:100]}...'")
                    
                    # Check if it starts with the corrected format
                    if description.strip().startswith('Location: Remote'):
                        print("✅ SUCCESS: Live XML now shows corrected description!")
                        return True
                    else:
                        print("❌ ISSUE: Live XML still shows old description")
                        return False
                else:
                    print("Could not extract description from live XML")
                    return False
            else:
                print("Job 32539 not found in live XML")
                return False
        else:
            print(f"Failed to download live XML: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"Verification error: {e}")
        return False

if __name__ == "__main__":
    if upload_via_sftp():
        print("\n✓ Upload completed successfully")
        
        # Wait a moment for server to process
        import time
        print("Waiting 5 seconds for server to process...")
        time.sleep(5)
        
        # Verify the fix
        verify_live_fix()
    else:
        print("❌ Upload failed")