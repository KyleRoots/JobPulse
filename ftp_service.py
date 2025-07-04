import ftplib
import os
import logging
from typing import Optional

class FTPService:
    """Handles FTP uploads to WP Engine or other hosting providers"""
    
    def __init__(self, hostname: str, username: str, password: str, target_directory: str = "/"):
        """
        Initialize FTP service
        
        Args:
            hostname: FTP server hostname
            username: FTP username
            password: FTP password
            target_directory: Target directory on server (default: root)
        """
        self.hostname = hostname
        self.username = username
        self.password = password
        self.target_directory = target_directory.rstrip('/')
        
    def upload_file(self, local_file_path: str, remote_filename: Optional[str] = None) -> bool:
        """
        Upload file to FTP server
        
        Args:
            local_file_path: Path to local file to upload
            remote_filename: Remote filename (defaults to local filename)
            
        Returns:
            bool: True if upload successful, False otherwise
        """
        if not remote_filename:
            remote_filename = os.path.basename(local_file_path)
            
        remote_path = f"{self.target_directory}/{remote_filename}" if self.target_directory != "/" else remote_filename
        
        try:
            # Connect to FTP server
            logging.info(f"Connecting to FTP server: {self.hostname}")
            with ftplib.FTP(self.hostname) as ftp:
                # Login
                ftp.login(self.username, self.password)
                logging.info("FTP login successful")
                
                # Change to target directory if specified
                if self.target_directory != "/":
                    try:
                        ftp.cwd(self.target_directory)
                        logging.info(f"Changed to directory: {self.target_directory}")
                    except ftplib.error_perm as e:
                        logging.error(f"Could not change to directory {self.target_directory}: {e}")
                        return False
                
                # Upload file in binary mode
                with open(local_file_path, 'rb') as file:
                    result = ftp.storbinary(f'STOR {remote_filename}', file)
                    
                if result.startswith('226'):  # 226 Transfer complete
                    logging.info(f"File uploaded successfully: {remote_filename}")
                    return True
                else:
                    logging.error(f"Upload failed with result: {result}")
                    return False
                    
        except ftplib.error_perm as e:
            logging.error(f"FTP permission error: {e}")
            return False
        except ftplib.error_temp as e:
            logging.error(f"FTP temporary error: {e}")
            return False
        except Exception as e:
            logging.error(f"FTP upload error: {e}")
            return False
    
    def test_connection(self) -> bool:
        """
        Test FTP connection without uploading
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            with ftplib.FTP(self.hostname) as ftp:
                ftp.login(self.username, self.password)
                logging.info("FTP connection test successful")
                return True
        except Exception as e:
            logging.error(f"FTP connection test failed: {e}")
            return False
    
    def list_directory(self, directory: str = None) -> list:
        """
        List files in FTP directory
        
        Args:
            directory: Directory to list (defaults to current/target directory)
            
        Returns:
            list: List of files in directory
        """
        try:
            with ftplib.FTP(self.hostname) as ftp:
                ftp.login(self.username, self.password)
                
                if directory:
                    ftp.cwd(directory)
                elif self.target_directory != "/":
                    ftp.cwd(self.target_directory)
                
                files = ftp.nlst()
                logging.info(f"Directory listing successful: {len(files)} files found")
                return files
        except Exception as e:
            logging.error(f"Error listing directory: {e}")
            return []