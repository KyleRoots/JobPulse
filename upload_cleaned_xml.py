#!/usr/bin/env python3
"""
Upload cleaned XML files to SFTP
"""

import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Upload cleaned XML files to production"""
    from app import app
    
    with app.app_context():
        try:
            logger.info("=" * 60)
            logger.info("UPLOADING CLEANED XML FILES TO SFTP")
            logger.info("=" * 60)
            
            # Import FTP service
            from ftp_service import FTPService
            
            # Initialize service
            ftp_service = FTPService()
            
            # Upload both XML files
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            
            for xml_file in xml_files:
                logger.info(f"\nUploading {xml_file}...")
                
                try:
                    success = ftp_service.upload_file(xml_file)
                    
                    if success:
                        logger.info(f"✅ Successfully uploaded {xml_file}")
                    else:
                        logger.error(f"❌ Failed to upload {xml_file}")
                        
                except Exception as e:
                    logger.error(f"Error uploading {xml_file}: {str(e)}")
            
            logger.info("\n" + "=" * 60)
            logger.info("UPLOAD COMPLETE")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()