"""
iSync — State-based sync engine
Initial sync on startup, then incremental via state table diffs.
"""

import os
import time
import fnmatch
import logging
from typing import Dict, List, Optional

from config import SyncTask
from sftp_client import SFTPClient, FileInfo, TransferError
from state import StateManager

logger = logging.getLogger("isync.engine")


class SyncEngine:
    """State-driven sync: init full sync → incremental via state diffs."""

    def __init__(self, task: SyncTask, sftp: SFTPClient, display=None):
        self.task = task
        self.sftp = sftp
        self.display = display
        self.state = StateManager(task.local_path, task.name)

    # ── scan ──────────────────────────────────────────────────────

    def _scan_local(self) -> Dict[str, dict]:
        result = {}
        root = self.task.local_path
        if not os.path.isdir(root):
            return result
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not self._is_excluded(
                os.path.relpath(os.path.join(dirpath, d), root))]
            for f in filenames:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, root)
                if self._is_excluded(rel):
                    continue
                s = os.stat(full)
                result[rel] = {"size": s.st_size, "mtime": s.st_mtime}
        return result

    def _scan_remote(self) -> Dict[str, dict]:
        result = {}
        for path, fi in self.sftp.list_files(
            self.task.remote_path, exclude=self.task.exclude
        ).items():
            result[path] = {"size": fi.size, "mtime": fi.mtime}
        return result

    # ── initial sync ──────────────────────────────────────────────

    def initial_sync(self):
        """
        Full scan of both sides, transfer everything that differs,
        then save state snapshot. Runs once on startup.
        """
        logger.info("=== 初始同步: %s ===", self.task.name)

        local = self._scan_local()
        remote = self._scan_remote()

        logger.info("本地: %d 文件  远端: %d 文件", len(local), len(remote))

        # Simple merge: files only local → upload, only remote → download
        all_paths = set(local.keys()) | set(remote.keys())
        to_upload = []
        to_download = []

        for p in all_paths:
            in_local = p in local
            in_remote = p in remote
            if in_local and in_remote:
                if (local[p]["size"] != remote[p]["size"] or
                        abs(local[p]["mtime"] - remote[p]["mtime"]) > 2.0):
                    # Different — use conflict resolution
                    if self.task.direction == "remote-to-local":
                        to_download.append(p)
                    elif self.task.direction == "local-to-remote":
                        to_upload.append(p)
                    elif self.task.conflict_resolution == "remote":
                        to_download.append(p)
                    elif self.task.conflict_resolution == "local":
                        to_upload.append(p)
                    else:  # newer
                        if local[p]["mtime"] >= remote[p]["mtime"]:
                            to_upload.append(p)
                        else:
                            to_download.append(p)
            elif in_local:
                if self.task.direction != "remote-to-local":
                    to_upload.append(p)
            elif in_remote:
                if self.task.direction != "local-to-remote":
                    to_download.append(p)

        # Execute
        if to_upload or to_download:
            logger.info("初始传输: ↑%d ↓%d", len(to_upload), len(to_download))
            self._do_uploads(to_upload)
            self._do_downloads(to_download)

        # Save initial state snapshot — both sides now identical
        merged = {}
        for p in all_paths:
            if p in local:
                merged[p] = local[p]
            else:
                merged[p] = remote[p]
        # Re-scan local after downloads to get actual local state
        local_after = self._scan_local()
        remote_after = self._scan_remote()
        self.state.set_local_snapshot(local_after)
        self.state.set_remote_snapshot(remote_after)
        self.state.save()
        logger.info("状态已保存: local=%d remote=%d",
                     len(local_after), len(remote_after))

    # ── incremental: local → remote ───────────────────────────────

    def sync_local_change(self, rel_path: str, event_type: str):
        """
        Handle a local file change detected by FileWatcher.
        Compares against state to decide what to do.
        """
        local_path = os.path.join(self.task.local_path, rel_path)
        remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"

        if event_type in ("created", "modified"):
            if not os.path.isfile(local_path):
                return
            s = os.stat(local_path)
            stored = self.state.local_files.get(rel_path)
            if stored and stored["size"] == s.st_size and abs(stored["mtime"] - s.st_mtime) < 2.0:
                return  # unchanged per state
            # Upload
            try:
                self.sftp.upload(local_path, remote_path)
                self.state.update_local_file(rel_path, s.st_size, s.st_mtime)
                self.state.save()
                logger.info("↑ %s", rel_path)
            except Exception as e:
                logger.error("Upload failed: %s — %s", rel_path, e)

        elif event_type == "deleted":
            if not self.task.delete_propagate:
                return
            stored = self.state.local_files.get(rel_path)
            if not stored:
                return  # never tracked
            try:
                self.sftp.delete(remote_path)
                self.state.remove_local_file(rel_path)
                self.state.save()
                logger.info("✗ %s", rel_path)
            except Exception as e:
                logger.error("Delete remote failed: %s — %s", rel_path, e)

    # ── incremental: remote → local ───────────────────────────────

    def poll_remote_changes(self):
        """
        Scan remote, diff against stored remote state,
        download additions/modifications, delete local for remote deletions.
        """
        current = self._scan_remote()
        diff = self.state.diff_remote(current)

        if diff["added"] or diff["modified"] or diff["deleted"]:
            logger.info("远端变化: +%d新 ~%d改 -%d删 (共%d文件)",
                         len(diff["added"]), len(diff["modified"]),
                         len(diff["deleted"]), len(current))

        # Download new + modified
        for p in diff["added"] + diff["modified"]:
            if self.task.direction == "local-to-remote":
                continue
            try:
                self._download_one(p)
                self.state.update_remote_file(p, current[p]["size"], current[p]["mtime"])
            except Exception as e:
                logger.error("Download failed: %s — %s", p, e)

        # Delete local for remote deletions
        if self.task.delete_propagate and self.task.direction != "local-to-remote":
            for p in diff["deleted"]:
                local_path = os.path.join(self.task.local_path, p)
                try:
                    if os.path.isfile(local_path):
                        os.remove(local_path)
                    self.state.remove_remote_file(p)
                    logger.info("✗ %s (远端已删除)", p)
                except Exception as e:
                    logger.error("Local delete failed: %s — %s", p, e)

        if diff["added"] or diff["modified"] or diff["deleted"]:
            self.state.save()
        return diff

    # ── helpers ───────────────────────────────────────────────────

    def _do_uploads(self, paths: List[str]):
        root = self.task.local_path
        remote_root = self.task.remote_path.rstrip("/")
        for p in paths:
            try:
                self.sftp.upload(os.path.join(root, p), f"{remote_root}/{p}")
                logger.info("↑ %s", p)
            except Exception as e:
                logger.error("Upload failed: %s — %s", p, e)

    def _do_downloads(self, paths: List[str]):
        remote_root = self.task.remote_path.rstrip("/")
        root = self.task.local_path
        for p in paths:
            try:
                self._download_one(p)
                logger.info("↓ %s", p)
            except Exception as e:
                logger.error("Download failed: %s — %s", p, e)

    def _download_one(self, rel_path: str):
        local_path = os.path.join(self.task.local_path, rel_path)
        remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self.sftp.download(remote_path, local_path)

    def _is_excluded(self, rel_path: str) -> bool:
        for pat in self.task.exclude:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            if fnmatch.fnmatch(os.path.basename(rel_path), pat):
                return True
        return False
