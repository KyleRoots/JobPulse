import ftplib
import paramiko
import os
import socket
import time
import logging
from contextlib import contextmanager
logger = logging.getLogger(__name__)
from typing import Optional

# WP Engine's SFTP endpoint intermittently rejects authentication when a single
# upload cycle opens several connections in quick succession. Observed Jul 30
# 2026: the first file of the cycle uploaded fine, the next two were rejected
# with "Authentication failed" one second later, and the server tarpitted the
# third connection for 21s before rejecting it. Credentials were unchanged and
# the following cycle succeeded, so connect-stage failures are treated as
# transient rather than fatal.
DEFAULT_CONNECT_ATTEMPTS = 3
DEFAULT_CONNECT_RETRY_DELAY = 2.0
DEFAULT_CONNECT_RETRY_MULTIPLIER = 3.0

# Failures that mean "try again", as opposed to a bad path or a bad payload.
TRANSIENT_CONNECT_ERRORS = (
    paramiko.AuthenticationException,
    paramiko.SSHException,
    socket.timeout,
    socket.error,
    EOFError,
)


class FTPService:
    """Handles FTP and SFTP uploads to WP Engine or other hosting providers"""
    
    def __init__(self, hostname: str, username: str, password: str, target_directory: str = "/", 
                 port: Optional[int] = None, use_sftp: bool = False,
                 connect_attempts: int = DEFAULT_CONNECT_ATTEMPTS,
                 connect_retry_delay: float = DEFAULT_CONNECT_RETRY_DELAY):
        """
        Initialize FTP/SFTP service
        
        Args:
            hostname: FTP/SFTP server hostname
            username: FTP/SFTP username
            password: FTP/SFTP password
            target_directory: Target directory on server (default: root)
            port: Port number (default: 21 for FTP, 22 for SFTP)
            use_sftp: Whether to use SFTP instead of FTP
            connect_attempts: Total SFTP connect attempts before giving up
            connect_retry_delay: Seconds before the first connect retry
        """
        self.hostname = hostname
        self.username = username
        self.password = password
        # Handle target directory - preserve "/" as root, strip trailing slashes from other paths
        self.target_directory = target_directory.rstrip('/') if target_directory != "/" else "/"
        self.use_sftp = use_sftp
        self.port = port if port is not None else (2222 if use_sftp else 21)
        self.connect_attempts = max(1, connect_attempts)
        self.connect_retry_delay = connect_retry_delay
        # Reason for the most recent upload failure, so callers reporting to a
        # human can say what went wrong instead of "returned False".
        self.last_error: Optional[str] = None
        # Set while a sftp_session() block is active so uploads inside it share
        # one authenticated connection instead of re-authenticating per file.
        self._session_sftp = None
        
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

        self.last_error = None
        if self.use_sftp:
            return self._upload_sftp(local_file_path, remote_filename)
        else:
            return self._upload_ftp(local_file_path, remote_filename)
    
    def _upload_ftp(self, local_file_path: str, remote_filename: str) -> bool:
        """Upload file using FTP with timeout protection"""
        try:
            logger.info(f"Connecting to FTP server: {self.hostname}:{self.port}")
            with ftplib.FTP() as ftp:
                # Set generous timeouts to handle slow connections
                ftp.set_debuglevel(0)
                ftp.connect(self.hostname, self.port, timeout=120)  # Increased to 2 minutes
                ftp.login(self.username, self.password)
                logger.info("FTP login successful")
                
                # Change to target directory if specified
                if self.target_directory != "/":
                    try:
                        ftp.cwd(self.target_directory)
                        logger.info(f"Changed to directory: {self.target_directory}")
                    except ftplib.error_perm as e:
                        logger.error(f"Could not change to directory {self.target_directory}: {e}")
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
                        logger.info(f"File uploaded successfully via FTP: {remote_filename}")
                        
                        # Post-upload verification
                        try:
                            size = ftp.size(remote_filename)
                            local_size = os.path.getsize(local_file_path)
                            if size == local_size:
                                logger.info(f"Upload verified: {remote_filename} ({size} bytes)")
                                return True
                            else:
                                logger.error(f"Upload verification failed: remote {size} != local {local_size}")
                                return False
                        except Exception:
                            # If SIZE command not supported, consider upload successful
                            logger.warning("Unable to verify upload size - assuming success")
                            return True
                    else:
                        logger.error(f"FTP upload failed with result: {result}")
                        return False
                except (socket.timeout, socket.error) as e:
                    logger.error(f"FTP upload timeout/socket error after 90 seconds for {remote_filename}: {e}")
                    return False
                    
        except ftplib.error_perm as e:
            logger.error(f"FTP permission error: {e}")
            return False
        except ftplib.error_temp as e:
            logger.error(f"FTP temporary error: {e}")
            return False
        except Exception as e:
            logger.error(f"FTP upload error: {e}")
            return False
    
    def _connect_sftp(self):
        """Open an authenticated SFTP connection, retrying transient failures.

        Returns (ssh, sftp) on success. Raises the last error if every attempt
        fails, so callers can report the real reason.
        """
        delay = self.connect_retry_delay
        last_error = None

        for attempt in range(1, self.connect_attempts + 1):
            ssh = None
            try:
                logger.info(
                    f"Connecting to SFTP server: {self.hostname}:{self.port} "
                    f"(attempt {attempt}/{self.connect_attempts})"
                )
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    hostname=self.hostname,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=30
                )
                sftp = ssh.open_sftp()
                if attempt > 1:
                    logger.info(
                        f"SFTP connection successful on attempt {attempt} "
                        f"after a transient failure"
                    )
                else:
                    logger.info("SFTP connection successful")
                return ssh, sftp
            except TRANSIENT_CONNECT_ERRORS as e:
                last_error = e
                if ssh is not None:
                    try:
                        ssh.close()
                    except Exception:
                        pass
                if attempt < self.connect_attempts:
                    logger.warning(
                        f"SFTP connect failed ({type(e).__name__}: {e}); "
                        f"retrying in {delay:.0f}s"
                    )
                    time.sleep(delay)
                    delay *= DEFAULT_CONNECT_RETRY_MULTIPLIER
                else:
                    logger.error(
                        f"SFTP connect failed after {self.connect_attempts} "
                        f"attempts ({type(e).__name__}: {e})"
                    )

        raise last_error

    @contextmanager
    def sftp_session(self):
        """Reuse one authenticated SFTP connection for several uploads.

        Uploading each feed on its own connection meant three connect-and-auth
        handshakes within a few seconds every cycle, which is what the remote
        server was throttling. Inside this block, uploads share one connection,
        so a cycle authenticates once.

        Not thread-safe: give each concurrent uploader its own FTPService.
        """
        if not self.use_sftp:
            yield self
            return

        ssh, sftp = self._connect_sftp()
        try:
            # Fail fast on a bad target directory, matching the single-file
            # path, rather than surfacing it once per feed as a put() error.
            if self.target_directory != "/":
                sftp.chdir(self.target_directory)
                logger.info(f"Changed to directory: {self.target_directory}")
        except Exception:
            for conn in (sftp, ssh):
                try:
                    conn.close()
                except Exception:
                    pass
            raise

        self._session_sftp = sftp
        try:
            yield self
        finally:
            self._session_sftp = None
            for conn in (sftp, ssh):
                try:
                    conn.close()
                except Exception:
                    pass

    def _put_and_verify(self, sftp, local_file_path: str, remote_filename: str) -> bool:
        """Write one file over an open SFTP connection and size-check it."""
        if self.target_directory != "/":
            remote_path = f"{self.target_directory}/{remote_filename}"
        else:
            remote_path = remote_filename

        local_size = os.path.getsize(local_file_path)
        sftp.put(local_file_path, remote_path)

        remote_size = sftp.stat(remote_path).st_size
        if local_size == remote_size:
            logger.info(
                f"File uploaded successfully via SFTP: {remote_filename} "
                f"(Size: {local_size} bytes / {local_size/1024:.1f} KB)"
            )
        else:
            logger.error(
                f"SFTP upload size mismatch for {remote_filename}! "
                f"Local: {local_size} bytes, Remote: {remote_size} bytes"
            )
            logger.warning("Continuing despite size mismatch - file may need re-upload")
        return True

    def _upload_sftp(self, local_file_path: str, remote_filename: str) -> bool:
        """Upload file using SFTP, reusing an open session when one is active."""
        if self._session_sftp is not None:
            try:
                return self._put_and_verify(
                    self._session_sftp, local_file_path, remote_filename
                )
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error(
                    f"SFTP upload error on shared session "
                    f"({type(e).__name__}): {e}"
                )
                return False

        ssh = sftp = None
        try:
            ssh, sftp = self._connect_sftp()

            # Change to target directory if specified
            if self.target_directory != "/":
                try:
                    sftp.chdir(self.target_directory)
                    logger.info(f"Changed to directory: {self.target_directory}")
                except Exception as e:
                    self.last_error = f"Could not change to directory {self.target_directory}: {e}"
                    logger.error(self.last_error)
                    return False

            return self._put_and_verify(sftp, local_file_path, remote_filename)

        except paramiko.AuthenticationException as e:
            self.last_error = f"SFTP authentication failed: {e}"
            logger.error(self.last_error)
            return False
        except paramiko.SSHException as e:
            self.last_error = f"SFTP SSH error: {e}"
            logger.error(self.last_error)
            return False
        except IOError as e:
            # IOError often indicates permission denied or file not found on remote
            self.last_error = f"SFTP IOError (possible permission/path issue): {e}"
            logger.error(self.last_error)
            return False
        except Exception as e:
            # Log the full exception type for debugging
            import traceback
            self.last_error = f"SFTP upload error ({type(e).__name__}): {e}"
            logger.error(self.last_error)
            logger.error(f"SFTP upload traceback: {traceback.format_exc()}")
            return False
        finally:
            for conn in (sftp, ssh):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
    
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
                logger.info("FTP connection test successful")
                return True
        except Exception as e:
            logger.error(f"FTP connection test failed: {e}")
            return False
    
    def _test_sftp_connection(self) -> dict:
        """Test SFTP connection and return result with error details"""
        try:
            import paramiko
            
            # Create SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            logger.info(f"Testing SFTP connection to {self.hostname}:{self.port} as {self.username}")
            
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
            
            logger.info("SFTP connection test successful")
            return {'success': True, 'message': 'Connection successful'}
            
        except paramiko.AuthenticationException as e:
            logger.error(f"SFTP authentication failed: {e}")
            return {'success': False, 'error': f'Authentication failed: Invalid username or password'}
        except paramiko.SSHException as e:
            logger.error(f"SFTP SSH error: {e}")
            return {'success': False, 'error': f'SSH error: {str(e)}'}
        except socket.timeout:
            logger.error(f"SFTP connection timeout to {self.hostname}:{self.port}")
            return {'success': False, 'error': f'Connection timeout - check hostname and port'}
        except socket.gaierror as e:
            logger.error(f"SFTP DNS resolution failed: {e}")
            return {'success': False, 'error': f'DNS resolution failed: Cannot resolve hostname {self.hostname}'}
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"SFTP connection test failed ({error_type}): {e}")
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
                logger.info(f"Directory listing successful: {len(files)} files found")
                return files
        except Exception as e:
            logger.error(f"Error listing directory: {e}")
            return []