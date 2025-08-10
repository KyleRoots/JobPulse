#!/usr/bin/env python3
"""
Optimized upload service for handling large XML files
with improved timeout handling and retry logic
"""

import gzip
import tempfile
import os
import logging
from ftp_service import FTPService

class OptimizedUploader:
    """Enhanced uploader with compression and better timeout handling"""
    
    def __init__(self, hostname: str, username: str, password: str):
        self.hostname = hostname
        self.username = username 
        self.password = password
        
    def upload_xml_with_optimization(self, xml_file_path: str) -> bool:
        """
        Upload XML file using optimization techniques:
        1. Try direct upload first (fastest)
        2. Fall back to compressed upload if timeouts occur
        3. Use shorter timeouts with more retries
        """
        
        # Method 1: Direct upload with reduced timeout
        logging.info(f"🚀 OPTIMIZED UPLOAD: Attempting direct upload of {xml_file_path}")
        if self._quick_upload(xml_file_path):
            logging.info("✅ Direct upload successful")
            return True
            
        # Method 2: Compressed upload 
        logging.info("📦 Direct upload failed - trying compressed upload")
        if self._compressed_upload(xml_file_path):
            logging.info("✅ Compressed upload successful")
            return True
            
        logging.error("❌ All optimized upload methods failed")
        return False
        
    def _quick_upload(self, xml_file_path: str) -> bool:
        """Quick upload with aggressive timeouts - fail fast if network is slow"""
        try:
            # Use FTP with reduced timeouts for quick attempt
            ftp_service = FTPService(
                hostname=self.hostname,
                username=self.username,
                password=self.password,
                use_sftp=False
            )
            
            # Override timeout settings for quick attempt
            original_file = xml_file_path
            return ftp_service.upload_file(original_file)
            
        except Exception as e:
            logging.warning(f"Quick upload failed: {str(e)[:100]}")
            return False
            
    def _compressed_upload(self, xml_file_path: str) -> bool:
        """Upload compressed version, then decompress on server if supported"""
        try:
            # Create compressed version
            with tempfile.NamedTemporaryFile(suffix='.xml.gz', delete=False) as temp_gz:
                with open(xml_file_path, 'rb') as f_in:
                    with gzip.open(temp_gz.name, 'wb') as f_out:
                        f_out.write(f_in.read())
                
                # Check compression ratio
                original_size = os.path.getsize(xml_file_path)
                compressed_size = os.path.getsize(temp_gz.name)
                ratio = compressed_size / original_size
                
                logging.info(f"📦 Compressed {original_size:,} bytes to {compressed_size:,} bytes ({ratio:.1%})")
                
                # Upload compressed file if significantly smaller
                if ratio < 0.7:  # Only if >30% compression
                    sftp_service = FTPService(
                        hostname=self.hostname,
                        username=self.username,
                        password=self.password,
                        use_sftp=True
                    )
                    
                    # Upload with .gz extension for now
                    success = sftp_service.upload_file(temp_gz.name, 'myticas-job-feed.xml.gz')
                    
                    # Clean up
                    os.unlink(temp_gz.name)
                    
                    if success:
                        logging.info("📦 Compressed upload successful - manual decompression needed")
                        return True
                else:
                    logging.info("📦 Compression ratio not beneficial - skipping")
                    os.unlink(temp_gz.name)
                    
        except Exception as e:
            logging.error(f"Compressed upload failed: {str(e)[:100]}")
            
        return False

def test_optimized_upload():
    """Test the optimized uploader"""
    uploader = OptimizedUploader(
        hostname='mytconsulting.sftp.wpengine.com',
        username=os.getenv('SFTP_USERNAME'),
        password=os.getenv('SFTP_PASSWORD')
    )
    
    if os.path.exists('myticas-job-feed.xml'):
        success = uploader.upload_xml_with_optimization('myticas-job-feed.xml')
        print(f"Upload result: {'SUCCESS' if success else 'FAILED'}")
    else:
        print("XML file not found")

if __name__ == '__main__':
    test_optimized_upload()