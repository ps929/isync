"""
iSync - SFTP client wrapper
Provides a unified interface for SSH/SFTP operations using paramiko.
"""

import os
import stat
import hashlib
import fnmatch
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import paramiko

logger = logging.getLogger("isync.sftp")


@dataclass
class FileInfo:
    """Unified file metadata for comparison."""
    path: str          # relative path within the sync root
    size: int          # file size in bytes
    mtime: float       # modification timestamp (epoch)
    is_dir: bool = False
    quick_hash: Optional[str] = None  # SHA256 partial hash (head+tail+size)

    def __eq__(self, other: "FileInfo") -> bool:
        if not isinstance(other, FileInfo):
            return False
        if self.size != other.size:
            return False
        # If both sides have content hashes, use them (most reliable)
        if self.quick_hash is not None and other.quick_hash is not None:
            return self.quick_hash == other.quick_hash
        # Fall back to mtime tolerance
        # NOTE: 2-second mtime tolerance accounts for FAT/exFAT/SFTP timestamp
        # granularity. For best accuracy, use comparison: "content" mode.
        return abs(self.mtime - other.mtime) < 2.0

    def __hash__(self):
        return hash((self.path, self.size, int(self.mtime)))


class SFTPError(Exception):
    """Base exception for SFTP client errors."""


class ConnectionError(SFTPError):
    """SSH connection error."""


class TransferError(SFTPError):
    """File transfer error."""


class SFTPClient:
    """
    SSH/SFTP client wrapper supporting key and password authentication.
    Can be used as a context manager.
    """

    def __init__(self, host: str, port: int = 22, user: str = "",
                 auth_type: str = "key", password: str = "",
                 ssh_key_path: str = "~/.ssh/id_rsa"):
        self.host = host
        self.port = port
        self.user = user
        self.auth_type = auth_type
        self.password = password
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        # Store connection params for auto-reconnect
        self._connect_kwargs = {
            "hostname": self.host, "port": self.port,
            "username": self.user,
            "look_for_keys": False, "allow_agent": False,
            "timeout": 15,
        }
        if self.auth_type == "password":
            self._connect_kwargs["password"] = self.password
        else:
            self._connect_kwargs["key_filename"] = self.ssh_key_path

    # ── context manager ──────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    # ── connection management ─────────────────────────────────────

    def connect(self):
        """Establish SSH connection and open SFTP channel."""
        logger.info("Connecting to %s@%s:%d ...", self.user, self.host, self.port)

        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if self.auth_type == "password":
                self._ssh.connect(
                    hostname=self.host, port=self.port,
                    username=self.user, password=self.password,
                    look_for_keys=False, allow_agent=False,
                    timeout=15,
                )
            else:
                # key-based authentication
                if not os.path.exists(self.ssh_key_path):
                    raise ConnectionError(
                        f"SSH key not found: {self.ssh_key_path}"
                    )
                self._ssh.connect(
                    hostname=self.host, port=self.port,
                    username=self.user, key_filename=self.ssh_key_path,
                    look_for_keys=False, allow_agent=False,
                    timeout=15,
                )

            self._sftp = self._ssh.open_sftp()
            logger.info("Connected to %s successfully.", self.host)

        except paramiko.AuthenticationException as e:
            raise ConnectionError(f"Authentication failed for {self.user}@{self.host}: {e}") from e
        except paramiko.SSHException as e:
            raise ConnectionError(f"SSH connection error: {e}") from e
        except OSError as e:
            raise ConnectionError(f"Network error connecting to {self.host}:{self.port}: {e}") from e

    def disconnect(self):
        """Close SFTP channel and SSH connection."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None

        logger.info("Disconnected from %s.", self.host)

    @property
    def is_connected(self) -> bool:
        if self._ssh is None or self._sftp is None:
            return False
        # Verify the transport is still alive
        try:
            transport = self._ssh.get_transport()
            if transport is None or not transport.is_active():
                return False
        except Exception:
            return False
        return True

    def reconnect(self):
        """Disconnect and re-establish the SSH/SFTP connection."""
        logger.info("Reconnecting to %s@%s:%d ...", self.user, self.host, self.port)

        # Clean up old connection
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None

        # Brief pause to let old socket fully close
        import time
        time.sleep(0.5)

        # Re-establish
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._ssh.connect(**self._connect_kwargs)
            self._sftp = self._ssh.open_sftp()
        except Exception as e:
            self._ssh = None
            self._sftp = None
            raise ConnectionError(f"Reconnect failed: {e}") from e

        logger.info("Reconnected to %s successfully.", self.host)

    def _ensure_connected(self):
        """Check connection; auto-reconnect once if disconnected."""
        if self.is_connected:
            return
        logger.warning("SFTP connection lost. Attempting reconnect...")
        self.reconnect()

    def _retry(self, operation_name: str, func, *args, **kwargs):
        """
        Call func(); on connection-related failure, reconnect and retry once.

        Only retries on actual connection errors, NOT on file-not-found
        or permission errors (which indicate the remote state, not a
        connection problem).
        """
        try:
            return func(*args, **kwargs)
        except FileNotFoundError:
            raise  # not a connection error, don't retry
        except PermissionError:
            raise  # not a connection error, don't retry
        except (paramiko.ssh_exception.SSHException, OSError, EOFError,
                ConnectionError, TransferError) as e:
            logger.warning("%s failed (connection): %s — reconnecting...", operation_name, e)
            try:
                self.reconnect()
            except Exception as reconnect_err:
                logger.error("Reconnect also failed: %s", reconnect_err)
                raise TransferError(
                    f"{operation_name} failed and reconnect also failed: {reconnect_err}"
                ) from e
            # Retry once
            return func(*args, **kwargs)

    # ── file operations ───────────────────────────────────────────

    def list_files(self, remote_path: str, exclude: Optional[List[str]] = None) -> Dict[str, FileInfo]:
        """
        Recursively list all files under remote_path.
        Returns a dict mapping relative path -> FileInfo.
        """
        self._ensure_connected()
        exclude = exclude or []
        result: Dict[str, FileInfo] = {}
        remote_path = remote_path.rstrip("/")

        def _walk(path: str):
            try:
                for entry in self._sftp.listdir_attr(path):
                    name = entry.filename
                    full = f"{path}/{name}"
                    rel = os.path.relpath(full, remote_path)

                    # Skip some common hidden artifacts
                    if name.startswith(".") and name not in (".", ".."):
                        if name in (".DS_Store", ".git"):
                            continue

                    # Apply exclude patterns (relative path)
                    if self._match_exclude(rel, exclude):
                        logger.debug("Excluding remote: %s", rel)
                        continue

                    if stat.S_ISDIR(entry.st_mode):
                        _walk(full)
                    else:
                        result[rel] = FileInfo(
                            path=rel,
                            size=entry.st_size,
                            mtime=entry.st_mtime,
                        )
            except FileNotFoundError:
                logger.warning("Remote path not found: %s", path)
            except PermissionError:
                logger.warning("Permission denied on remote: %s", path)

        def _do_list():
            _walk(remote_path)

        self._retry("list_files", _do_list)
        logger.debug("Scanned remote: %d files in %s", len(result), remote_path)
        return result

    def upload(self, local_path: str, remote_path: str,
               callback=None):
        """Upload a single file, creating parent directories on remote as needed.
        callback(bytes_done, total) is called for progress display."""
        self._ensure_connected()
        remote_dir = os.path.dirname(remote_path)
        self._mkdir_p(remote_dir)

        def _do():
            self._sftp.put(local_path, remote_path, callback=callback)

        try:
            self._retry("upload", _do)
            logger.debug("Uploaded: %s -> %s", local_path, remote_path)
        except Exception as e:
            raise TransferError(f"Failed to upload {local_path}: {e}") from e

    def download(self, remote_path: str, local_path: str,
                 callback=None):
        """Download a single file, creating parent directories locally as needed.
        callback(bytes_done, total) is called for progress display."""
        self._ensure_connected()
        local_dir = os.path.dirname(local_path)
        os.makedirs(local_dir, exist_ok=True)

        def _do():
            self._sftp.get(remote_path, local_path, callback=callback)

        try:
            self._retry("download", _do)
            logger.debug("Downloaded: %s -> %s", remote_path, local_path)
        except Exception as e:
            raise TransferError(f"Failed to download {remote_path}: {e}") from e

    def delete(self, remote_path: str):
        """Delete a remote file."""
        self._ensure_connected()

        def _do():
            self._sftp.remove(remote_path)

        try:
            self._retry("delete", _do)
            logger.debug("Deleted remote: %s", remote_path)
        except FileNotFoundError:
            logger.debug("Remote file already gone: %s", remote_path)
        except Exception as e:
            raise TransferError(f"Failed to delete remote {remote_path}: {e}") from e

    def mkdir(self, remote_path: str):
        """Recursively create a remote directory."""
        self._ensure_connected()
        self._mkdir_p(remote_path)

    def _mkdir_p(self, remote_path: str):
        """Internal: recursively create remote directory (like mkdir -p)."""
        if remote_path in ("", "/", "."):
            return
        dirs = []
        while remote_path and remote_path != "/":
            try:
                self._sftp.stat(remote_path)
                break  # exists
            except FileNotFoundError:
                dirs.append(remote_path)
                remote_path = os.path.dirname(remote_path)

        # create from shallowest to deepest
        for d in reversed(dirs):
            try:
                self._sftp.mkdir(d)
                logger.debug("Created remote dir: %s", d)
            except Exception as e:
                logger.debug("mkdir %s: %s (may already exist)", d, e)

    def get_info(self, remote_path: str) -> Optional[FileInfo]:
        """Get FileInfo for a single remote path."""
        self._ensure_connected()

        def _do():
            return self._sftp.stat(remote_path)

        try:
            attr = self._retry("get_info", _do)
            return FileInfo(
                path=os.path.basename(remote_path),
                size=attr.st_size,
                mtime=attr.st_mtime,
                is_dir=stat.S_ISDIR(attr.st_mode),
            )
        except FileNotFoundError:
            return None

    # ── content hashing ────────────────────────────────────────────

    def hash_chunks(self, remote_path: str,
                    head_bytes: int = 65536,
                    tail_bytes: int = 65536) -> str:
        """
        Compute a partial SHA256 hash of a remote file by reading
        only the head and tail chunks. Returns hex digest.

        Does NOT download the entire file — reads at most
        (head_bytes + tail_bytes) bytes over SFTP.
        """
        self._ensure_connected()
        try:
            attr = self._sftp.stat(remote_path)
            file_size = attr.st_size
        except FileNotFoundError:
            return ""

        read_head = min(head_bytes, file_size)
        read_tail = min(tail_bytes, max(0, file_size - read_head))

        with self._sftp.open(remote_path, 'rb') as f:
            head = f.read(read_head) if read_head > 0 else b''
            if read_tail > 0:
                f.seek(file_size - read_tail)
                tail = f.read(read_tail)
            else:
                tail = b''

        return self._compute_quick_hash(head, tail, file_size)

    @staticmethod
    def _compute_quick_hash(head: bytes, tail: bytes, file_size: int) -> str:
        """SHA256 of (head + tail + file_size_be)."""
        h = hashlib.sha256()
        h.update(head)
        h.update(tail)
        h.update(file_size.to_bytes(8, 'big'))
        return h.hexdigest()

    @staticmethod
    def compute_local_hash(local_path: str,
                           head_bytes: int = 65536,
                           tail_bytes: int = 65536) -> str:
        """
        Compute partial SHA256 hash of a local file.
        Same algorithm as hash_chunks for remote files.
        """
        try:
            file_size = os.path.getsize(local_path)
        except OSError:
            return ""

        read_head = min(head_bytes, file_size)
        read_tail = min(tail_bytes, max(0, file_size - read_head))

        with open(local_path, 'rb') as f:
            head = f.read(read_head) if read_head > 0 else b''
            if read_tail > 0:
                f.seek(file_size - read_tail)
                tail = f.read(read_tail)
            else:
                tail = b''

        h = hashlib.sha256()
        h.update(head)
        h.update(tail)
        h.update(file_size.to_bytes(8, 'big'))
        return h.hexdigest()

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _match_exclude(path: str, patterns: List[str]) -> bool:
        """Check if a relative path matches any exclude pattern."""
        for pat in patterns:
            # Support both fnmatch glob and ** patterns
            if fnmatch.fnmatch(path, pat):
                return True
            # Also check just the filename for simple patterns like *.tmp
            if fnmatch.fnmatch(os.path.basename(path), pat):
                return True
        return False
