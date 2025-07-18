import paramiko
import time
import os
from app import app, db
from models import GlobalSettings

def test_sftp_connection():
    """Test SFTP connection with detailed diagnostics"""
    with app.app_context():
        # Get SFTP credentials from database
        sftp_hostname = GlobalSettings.query.filter_by(setting_key='sftp_hostname').first()
        sftp_username = GlobalSettings.query.filter_by(setting_key='sftp_username').first()
        sftp_password = GlobalSettings.query.filter_by(setting_key='sftp_password').first()
        sftp_port = GlobalSettings.query.filter_by(setting_key='sftp_port').first()
        sftp_directory = GlobalSettings.query.filter_by(setting_key='sftp_directory').first()
        
        print("=== SFTP Configuration from Database ===")
        print(f"Hostname: {sftp_hostname.setting_value if sftp_hostname else 'Not found'}")
        print(f"Username: {sftp_username.setting_value if sftp_username else 'Not found'}")
        print(f"Password: {'*' * (len(sftp_password.setting_value) - 4) + sftp_password.setting_value[-4:] if sftp_password and sftp_password.setting_value else 'Not found'}")
        print(f"Port: {sftp_port.setting_value if sftp_port else 'Default (2222)'}")
        print(f"Directory: {sftp_directory.setting_value if sftp_directory else '/'}")
        
        if not (sftp_hostname and sftp_username and sftp_password):
            print("\n❌ Missing required SFTP credentials")
            return False
            
        print("\n=== Testing SFTP Connection ===")
        
        try:
            # Set up SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connection parameters
            hostname = sftp_hostname.setting_value
            port = int(sftp_port.setting_value) if sftp_port and sftp_port.setting_value else 2222
            username = sftp_username.setting_value
            password = sftp_password.setting_value
            
            print(f"Connecting to {hostname}:{port}...")
            
            start_time = time.time()
            ssh.connect(
                hostname=hostname,
                port=port,
                username=username,
                password=password,
                timeout=30,
                banner_timeout=30,
                auth_timeout=30,
                look_for_keys=False,
                allow_agent=False
            )
            connection_time = time.time() - start_time
            
            print(f"✅ SSH connection established in {connection_time:.2f} seconds")
            
            # Open SFTP session
            sftp = ssh.open_sftp()
            print("✅ SFTP session opened successfully")
            
            # List current directory
            print("\n=== Testing Directory Access ===")
            files = sftp.listdir()
            print(f"Current directory contains {len(files)} items")
            if files:
                print(f"First 5 items: {files[:5]}")
            
            # Test file upload
            test_filename = "test_upload.txt"
            test_content = f"SFTP test upload at {time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            
            print(f"\n=== Testing File Upload ===")
            print(f"Creating test file: {test_filename}")
            
            # Create local test file
            with open(test_filename, 'w') as f:
                f.write(test_content)
            
            # Upload file
            try:
                sftp.put(test_filename, test_filename)
                print(f"✅ Successfully uploaded {test_filename}")
                
                # Verify file exists
                try:
                    sftp.stat(test_filename)
                    print(f"✅ Verified {test_filename} exists on server")
                    
                    # Clean up remote file
                    sftp.remove(test_filename)
                    print(f"✅ Cleaned up test file from server")
                except:
                    pass
                    
            except Exception as e:
                print(f"❌ Upload failed: {str(e)}")
            
            # Clean up local file
            os.remove(test_filename)
            
            # Test XML file upload
            xml_filename = "myticas-job-feed-dice.xml"
            if os.path.exists(xml_filename):
                print(f"\n=== Testing Production XML Upload ===")
                print(f"Uploading {xml_filename} ({os.path.getsize(xml_filename):,} bytes)...")
                
                try:
                    start_time = time.time()
                    sftp.put(xml_filename, xml_filename)
                    upload_time = time.time() - start_time
                    print(f"✅ Successfully uploaded {xml_filename} in {upload_time:.2f} seconds")
                except Exception as e:
                    print(f"❌ XML upload failed: {str(e)}")
            
            # Close connections
            sftp.close()
            ssh.close()
            
            print("\n✅ SFTP connection test completed successfully!")
            return True
            
        except paramiko.AuthenticationException as e:
            print(f"\n❌ Authentication failed: {str(e)}")
            print("Please check username and password")
            return False
            
        except paramiko.SSHException as e:
            print(f"\n❌ SSH error: {str(e)}")
            return False
            
        except Exception as e:
            print(f"\n❌ Connection error: {type(e).__name__}: {str(e)}")
            return False

if __name__ == "__main__":
    test_sftp_connection()