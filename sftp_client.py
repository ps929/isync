"""iSync — SSH/SFTP transport layer."""
import os, stat, hashlib, fnmatch, logging, time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import paramiko

logger = logging.getLogger("isync.sftp")

@dataclass
class FileInfo:
    path: str; size: int; mtime: float; is_dir: bool = False

class SFTPClient:
    """Manages SSH connection + SFTP operations with auto-reconnect."""

    def __init__(self, host: str, port: int, user: str,
                 auth_type: str = "password", password: str = "",
                 ssh_key_path: str = "~/.ssh/id_rsa"):
        self.host = host; self.port = port; self.user = user
        self.auth_type = auth_type; self.password = password
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def connect(self):
        logger.info("Connecting %s@%s:%d", self.user, self.host, self.port)
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw = dict(hostname=self.host, port=self.port, username=self.user,
                  look_for_keys=False, allow_agent=False, timeout=15)
        if self.auth_type == "password":
            kw["password"] = self.password
        else:
            kw["key_filename"] = self.ssh_key_path
        self._ssh.connect(**kw)
        self._sftp = self._ssh.open_sftp()
        t = self._ssh.get_transport()
        if t: t.set_keepalive(30)
        logger.info("Connected")

    def disconnect(self):
        if self._sftp:
            try: self._sftp.close()
            except: pass
            self._sftp = None
        if self._ssh:
            try: self._ssh.close()
            except: pass
            self._ssh = None

    @property
    def is_connected(self) -> bool:
        if not self._ssh or not self._sftp: return False
        try:
            t = self._ssh.get_transport()
            return t is not None and t.is_active()
        except: return False

    def reconnect(self):
        if self._sftp:
            try: self._sftp.close()
            except: pass
        if self._ssh:
            try: self._ssh.close()
            except: pass
        self._sftp = None; self._ssh = None
        time.sleep(0.5)
        self.connect()

    def _ensure(self):
        if not self.is_connected:
            logger.warning("Reconnecting...")
            self.reconnect()

    def list_files(self, remote_path: str, exclude: List[str] = None) -> Dict[str, FileInfo]:
        self._ensure()
        result = {}
        exclude = exclude or []
        base = remote_path.rstrip("/")

        def walk(path):
            try:
                for e in self._sftp.listdir_attr(path):
                    name = e.filename
                    full = f"{path}/{name}"
                    rel = os.path.relpath(full, base)
                    if _excluded(rel, exclude): continue
                    if stat.S_ISDIR(e.st_mode):
                        walk(full)
                    else:
                        result[rel] = FileInfo(rel, e.st_size, e.st_mtime)
            except (FileNotFoundError, PermissionError) as e:
                logger.debug("Skip %s: %s", path, e)

        walk(base)
        return result

    def upload(self, local: str, remote: str, callback=None):
        self._ensure()
        d = os.path.dirname(remote)
        self._mkdir_p(d)
        self._retry(lambda: self._sftp.put(local, remote, callback=callback))

    def download(self, remote: str, local: str, callback=None):
        self._ensure()
        os.makedirs(os.path.dirname(local), exist_ok=True)
        self._retry(lambda: self._sftp.get(remote, local, callback=callback))

    def delete(self, remote: str):
        self._ensure()
        try:
            self._sftp.remove(remote)
        except FileNotFoundError:
            pass

    def get_info(self, remote: str) -> Optional[FileInfo]:
        self._ensure()
        try:
            a = self._sftp.stat(remote)
            return FileInfo(os.path.basename(remote), a.st_size, a.st_mtime,
                            stat.S_ISDIR(a.st_mode))
        except FileNotFoundError:
            return None

    def _mkdir_p(self, path: str):
        if path in ("", "/", "."): return
        dirs = []
        while path and path != "/":
            try: self._sftp.stat(path); break
            except FileNotFoundError: dirs.append(path); path = os.path.dirname(path)
        for d in reversed(dirs):
            try: self._sftp.mkdir(d)
            except: pass

    def _retry(self, fn):
        try:
            return fn()
        except (paramiko.SSHException, OSError, EOFError) as e:
            if isinstance(e, (FileNotFoundError, PermissionError)): raise
            logger.warning("Retrying after error: %s", e)
            self.reconnect()
            return fn()


def _excluded(rel: str, patterns: List[str]) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p):
            return True
    return False
