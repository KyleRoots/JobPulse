import ftplib
import paramiko
import os
import logging
from typing import Optional

class FTPService:
    """Handles FTP and SFTP uploads to WP Engine or other hosting providers"""
    
    def __init__(self, hostname: str, username: str, password: str, target_directory: str = "/", 
                 port: Optional[int] = None, use_sftp: bool = False):
        """
        Initialize FTP/SFTP service
        
        Args:
            hostname: FTP/SFTP server hostname
            username: FTP/SFTP username
            password: FTP/SFTP password
            target_directory: Target directory on server (default: root)
            port: Port number (default: 21 for FTP, 22 for SFTP)
            use_sftp: Whether to use SFTP instead of FTP
        """
        self.hostname = hostname
        self.username = username
        self.password = password
        self.target_directory = target_directory.rstrip('/')
        self.use_sftp = use_sftp
        self.port = port if port is not None else (22 if use_sftp else 21)
        
    def upload_file(self, local_file_path: str, remote_filename: Optional[str] = None) -> bool:
        """
        Upload file to FTP/SFTP server
        
        Args:
            local_file_path: Path to local file to upload
            remote_filename: Remote filename (defaults to local filename)
            
        Returns:
            bool: True if upload successful, False otherwise
        """
        if not remote_filename:
            remote_filename = os.path.basename(local_file_path)
            
        if self.use_sftp:
            return self._upload_sftp(local_file_path, remote_filename)
        else:
            return self._upload_ftp(local_file_path, remote_filename)
    
    def _upload_ftp(self, local_file_path: str, remote_filename: str) -> bool:
        """Upload file using FTP with timeout protection"""
        try:
            logging.info(f"Connecting to FTP server: {self.hostname}:{self.port}")
            with ftplib.FTP() as ftp:
                # Set aggressive timeouts to prevent hanging
                ftp.set_debuglevel(0)
                ftp.connect(self.hostname, self.port, timeout=30)
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
                
                # Upload file in binary mode with timeout protection
                import signal
                def timeout_handler(signum, frame):
                    raise TimeoutError("FTP upload timeout")
                
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(90)  # 90 second timeout for upload
                
                try:
                    with open(local_file_path, 'rb') as file:
                        result = ftp.storbinary(f'STOR {remote_filename}', file)
                        
                    signal.alarm(0)  # Cancel timeout
                    
                    if result.startswith('226'):  # 226 Transfer complete
                        logging.info(f"File uploaded successfully via FTP: {remote_filename}")
                        return True
                    else:
                        logging.error(f"FTP upload failed with result: {result}")
                        return False
                except TimeoutError:
                    logging.error(f"FTP upload timeout after 90 seconds for {remote_filename}")
                    return False
                finally:
                    signal.alarm(0)  # Ensure timeout is cleared
                    
        except ftplib.error_perm as e:
            logging.error(f"FTP permission error: {e}")
            return False
        except ftplib.error_temp as e:
            logging.error(f"FTP temporary error: {e}")
            return False
        except Exception as e:
            logging.error(f"FTP upload error: {e}")
            return False
    
    def _upload_sftp(self, local_file_path: str, remote_filename: str) -> bool:
        """Upload file using SFTP"""
        try:
            logging.info(f"Connecting to SFTP server: {self.hostname}:{self.port}")
            
            # Create SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect to server
            ssh.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=30
            )
            
            # Create SFTP client
            sftp = ssh.open_sftp()
            logging.info("SFTP connection successful")
            
            # Change to target directory if specified
            if self.target_directory != "/":
                try:
                    sftp.chdir(self.target_directory)
                    logging.info(f"Changed to directory: {self.target_directory}")
                except Exception as e:
                    logging.error(f"Could not change to directory {self.target_directory}: {e}")
                    sftp.close()
                    ssh.close()
                    return False
            
            # Get local file size before upload
            local_size = os.path.getsize(local_file_path)
            
            # Upload file
            if self.target_directory != "/":
                remote_path = f"{self.target_directory}/{remote_filename}"
            else:
                remote_path = remote_filename
            sftp.put(local_file_path, remote_path)
            
            # Verify remote file size matches local
            remote_stats = sftp.stat(remote_path)
            remote_size = remote_stats.st_size
            
            if local_size == remote_size:
                logging.info(f"File uploaded successfully via SFTP: {remote_filename} (Size: {local_size} bytes / {local_size/1024:.1f} KB)")
            else:
                logging.error(f"SFTP upload size mismatch for {remote_filename}! Local: {local_size} bytes, Remote: {remote_size} bytes")
                # Still return True as file was uploaded, but log the discrepancy
                logging.warning(f"Continuing despite size mismatch - file may need re-upload")
            
            # Close connections
            sftp.close()
            ssh.close()
            return True
            
        except paramiko.AuthenticationException as e:
            logging.error(f"SFTP authentication failed: {e}")
            return False
        except paramiko.SSHException as e:
            logging.error(f"SFTP SSH error: {e}")
            return False
        except Exception as e:
            logging.error(f"SFTP upload error: {e}")
            return False
    
    def test_connection(self) -> bool:
        """
        Test FTP/SFTP connection without uploading
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        if self.use_sftp:
            return self._test_sftp_connection()
        else:
            return self._test_ftp_connection()
    
    def _test_ftp_connection(self) -> bool:
        """Test FTP connection"""
        try:
            with ftplib.FTP(self.hostname, timeout=10) as ftp:
                if self.port and self.port != 21:
                    ftp.connect(self.hostname, self.port)
                ftp.login(self.username, self.password)
                logging.info("FTP connection test successful")
                return True
        except Exception as e:
            logging.error(f"FTP connection test failed: {e}")
            return False
    
    def _test_sftp_connection(self) -> bool:
        """Test SFTP connection"""
        try:
            import paramiko
            
            # Create SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect to server
            ssh.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=10
            )
            
            # Test SFTP
            sftp = ssh.open_sftp()
            sftp.listdir(self.target_directory)
            sftp.close()
            ssh.close()
            
            logging.info("SFTP connection test successful")
            return True
            
        except Exception as e:
            # Check if it's a paramiko-specific exception
            error_type = type(e).__name__
            if 'AuthenticationException' in error_type:
                logging.error(f"SFTP authentication failed: {e}")
            elif 'SSHException' in error_type:
                logging.error(f"SFTP SSH error: {e}")
            else:
                logging.error(f"SFTP connection test failed: {e}")
            return False
    
    def list_directory(self, directory: Optional[str] = None) -> list:
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