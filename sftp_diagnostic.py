#!/usr/bin/env python3
"""
SFTP Diagnostic Tool - Check what files are on the server and their contents
"""
import os
import paramiko
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def check_sftp_files():
    """Check what XML files exist on the SFTP server and download them for comparison"""
    
    # Get SFTP credentials
    hostname = os.environ.get('SFTP_HOSTNAME') or os.environ.get('SFTP_HOST')
    username = os.environ.get('SFTP_USERNAME')
    password = os.environ.get('SFTP_PASSWORD')
    port = int(os.environ.get('SFTP_PORT', 2222))
    
    if not all([hostname, username, password]):
        logger.error(f"SFTP credentials not configured properly")
        return
    
    logger.info("=" * 70)
    logger.info("SFTP DIAGNOSTIC REPORT")
    logger.info("=" * 70)
    logger.info(f"\nConnecting to: {hostname}:{port}")
    logger.info(f"Username: {username}")
    logger.info("-" * 70)
    
    try:
        # Connect to SFTP
        transport = paramiko.Transport((hostname, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        
        # List all files in root directory
        logger.info("\n1. FILES IN ROOT DIRECTORY:")
        logger.info("-" * 40)
        files = sftp.listdir('/')
        xml_files = [f for f in files if f.endswith('.xml')]
        
        for file in xml_files:
            try:
                stat = sftp.stat(f'/{file}')
                size_kb = stat.st_size / 1024
                mod_time = datetime.fromtimestamp(stat.st_mtime)
                logger.info(f"   • /{file}")
                logger.info(f"     Size: {size_kb:.1f} KB")
                logger.info(f"     Modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info("")
            except Exception as e:
                logger.info(f"   • /{file} (could not stat: {e})")
        
        if not xml_files:
            logger.info("   No XML files found in root directory")
        
        # Check specific files we care about
        logger.info("\n2. CHECKING SPECIFIC FILES:")
        logger.info("-" * 40)
        
        target_files = [
            'myticas-job-feed.xml',
            'myticas-job-feed-v2.xml', 
            'myticas-job-feed-v2.xml.xml',  # Check for double extension
            'wp-content/uploads/myticas-job-feed-v2.xml'  # Check WordPress uploads
        ]
        
        for target_file in target_files:
            try:
                # Try to get file info
                if '/' in target_file:
                    stat = sftp.stat(f'/{target_file}')
                else:
                    stat = sftp.stat(f'/{target_file}')
                    
                size_kb = stat.st_size / 1024
                mod_time = datetime.fromtimestamp(stat.st_mtime)
                
                logger.info(f"   ✅ /{target_file} EXISTS")
                logger.info(f"      Size: {size_kb:.1f} KB")
                logger.info(f"      Modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Download first 2000 bytes to check format
                with sftp.open(f'/{target_file}', 'r') as remote_file:
                    sample = remote_file.read(2000).decode('utf-8', errors='ignore')
                    
                    # Check for old vs new format indicators
                    if '<publisher>' in sample:
                        logger.info(f"      Format: OLD (has <publisher> tag)")
                    elif '<source>' in sample and 'MYT-' in sample:
                        logger.info(f"      Format: NEW (has MYT- references)")
                    else:
                        logger.info(f"      Format: UNKNOWN")
                    
                    # Check for colon prefixes
                    if ':Full Stack' in sample or ':Senior' in sample:
                        logger.info(f"      Has colon prefixes: YES ⚠️")
                    else:
                        logger.info(f"      Has colon prefixes: NO ✅")
                        
                logger.info("")
                
            except FileNotFoundError:
                logger.info(f"   ❌ /{target_file} NOT FOUND")
            except Exception as e:
                logger.info(f"   ⚠️ /{target_file} ERROR: {str(e)}")
        
        # Try to find the actual path WordPress is serving from
        logger.info("\n3. SEARCHING FOR XML FILES IN COMMON LOCATIONS:")
        logger.info("-" * 40)
        
        search_paths = [
            'wp-content',
            'wp-content/uploads',
            'public_html',
            'htdocs',
            'www'
        ]
        
        for path in search_paths:
            try:
                files = sftp.listdir(f'/{path}')
                xml_in_path = [f for f in files if 'job-feed' in f.lower() and f.endswith('.xml')]
                if xml_in_path:
                    logger.info(f"   Found in /{path}:")
                    for xml_file in xml_in_path:
                        logger.info(f"      • {xml_file}")
            except:
                pass  # Directory doesn't exist
        
        # Download and save the actual file for comparison
        logger.info("\n4. DOWNLOADING myticas-job-feed-v2.xml FOR COMPARISON:")
        logger.info("-" * 40)
        
        try:
            local_path = 'downloaded_from_sftp.xml'
            sftp.get('/myticas-job-feed-v2.xml', local_path)
            
            with open(local_path, 'r') as f:
                content = f.read()
                
            job_count = content.count('<job>')
            has_publisher = '<publisher>' in content
            has_myt_refs = 'MYT-' in content
            has_colons = ':Full Stack' in content or ':Senior' in content
            
            logger.info(f"   ✅ Downloaded successfully to {local_path}")
            logger.info(f"   File analysis:")
            logger.info(f"      • Jobs: {job_count}")
            logger.info(f"      • Has <publisher> tag: {'YES (OLD FORMAT)' if has_publisher else 'NO'}")
            logger.info(f"      • Has MYT- references: {'YES' if has_myt_refs else 'NO'}")  
            logger.info(f"      • Has colon prefixes: {'YES ⚠️' if has_colons else 'NO ✅'}")
            
            # Compare with local file
            logger.info("\n5. COMPARING WITH LOCAL myticas-job-feed.xml:")
            logger.info("-" * 40)
            
            with open('myticas-job-feed.xml', 'r') as f:
                local_content = f.read()
                
            local_job_count = local_content.count('<job>')
            local_has_myt = 'MYT-' in local_content
            
            logger.info(f"   Local file:")
            logger.info(f"      • Jobs: {local_job_count}")
            logger.info(f"      • Has MYT- references: {'YES' if local_has_myt else 'NO'}")
            
            if content == local_content:
                logger.info(f"   ✅ SFTP file MATCHES local file")
            else:
                logger.info(f"   ❌ SFTP file DIFFERS from local file")
                logger.info(f"      Remote has {job_count} jobs, local has {local_job_count} jobs")
                
        except Exception as e:
            logger.info(f"   ❌ Could not download: {str(e)}")
        
        sftp.close()
        transport.close()
        
        logger.info("\n" + "=" * 70)
        logger.info("DIAGNOSTIC COMPLETE")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"\n❌ SFTP Connection Error: {str(e)}")
        logger.error("This might mean:")
        logger.error("  1. SFTP credentials are incorrect")
        logger.error("  2. Server is unreachable")
        logger.error("  3. Network timeout")

if __name__ == "__main__":
    check_sftp_files()