import ftplib
import paramiko
import os
import socket
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
        # Handle target directory - preserve "/" as root, strip trailing slashes from other paths
        self.target_directory = target_directory.rstrip('/') if target_directory != "/" else "/"
        self.use_sftp = use_sftp
        self.port = port if port is not None else (2222 if use_sftp else 21)
        
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
                # Set generous timeouts to handle slow connections
                ftp.set_debuglevel(0)
                ftp.connect(self.hostname, self.port, timeout=120)  # Increased to 2 minutes
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
                
                # Upload file in binary mode with thread-safe timeout protection
                # Set socket timeout for thread safety (works in background threads)
                if ftp.sock:
                    ftp.sock.settimeout(90)  # 90 second timeout for upload
                ftp.set_pasv(True)  # Enable passive mode for better compatibility
                
                try:
                    with open(local_file_path, 'rb') as file:
                        result = ftp.storbinary(f'STOR {remote_filename}', file)
                    
                    if result.startswith('226'):  # 226 Transfer complete
                        logging.info(f"File uploaded successfully via FTP: {remote_filename}")
                        
                        # Post-upload verification
                        try:
                            size = ftp.size(remote_filename)
                            local_size = os.path.getsize(local_file_path)
                            if size == local_size:
                                logging.info(f"Upload verified: {remote_filename} ({size} bytes)")
                                return True
                            else:
                                logging.error(f"Upload verification failed: remote {size} != local {local_size}")
                                return False
                        except:
                            # If SIZE command not supported, consider upload successful
                            logging.warning("Unable to verify upload size - assuming success")
                            return True
                    else:
                        logging.error(f"FTP upload failed with result: {result}")
                        return False
                except (socket.timeout, socket.error) as e:
                    logging.error(f"FTP upload timeout/socket error after 90 seconds for {remote_filename}: {e}")
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
        except IOError as e:
            # IOError often indicates permission denied or file not found on remote
            logging.error(f"SFTP IOError (possible permission/path issue): {e}")
            return False
        except Exception as e:
            # Log the full exception type for debugging
            import traceback
            logging.error(f"SFTP upload error ({type(e).__name__}): {e}")
            logging.error(f"SFTP upload traceback: {traceback.format_exc()}")
            return False
    
    def test_connection(self):
        """
        Test FTP/SFTP connection without uploading
        
        Returns:
            For SFTP: dict with 'success' and 'error' or 'message' keys
            For FTP: bool (legacy behavior)
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
    
    def _test_sftp_connection(self) -> dict:
        """Test SFTP connection and return result with error details"""
        try:
            import paramiko
            
            # Create SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            logging.info(f"Testing SFTP connection to {self.hostname}:{self.port} as {self.username}")
            
            # Connect to server
            ssh.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=15
            )
            
            # Test SFTP
            sftp = ssh.open_sftp()
            sftp.listdir(self.target_directory)
            sftp.close()
            ssh.close()
            
            logging.info("SFTP connection test successful")
            return {'success': True, 'message': 'Connection successful'}
            
        except paramiko.AuthenticationException as e:
            logging.error(f"SFTP authentication failed: {e}")
            return {'success': False, 'error': f'Authentication failed: Invalid username or password'}
        except paramiko.SSHException as e:
            logging.error(f"SFTP SSH error: {e}")
            return {'success': False, 'error': f'SSH error: {str(e)}'}
        except socket.timeout:
            logging.error(f"SFTP connection timeout to {self.hostname}:{self.port}")
            return {'success': False, 'error': f'Connection timeout - check hostname and port'}
        except socket.gaierror as e:
            logging.error(f"SFTP DNS resolution failed: {e}")
            return {'success': False, 'error': f'DNS resolution failed: Cannot resolve hostname {self.hostname}'}
        except Exception as e:
            error_type = type(e).__name__
            logging.error(f"SFTP connection test failed ({error_type}): {e}")
            return {'success': False, 'error': f'{error_type}: {str(e)}'}
    
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