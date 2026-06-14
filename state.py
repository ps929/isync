"""
iSync — State Manager
.isync_state.json records last-known file state + tracks
in-progress transfers to prevent duplicate sync operations.
"""

import os
import json
import time
import logging
import threading
from typing import Dict, Optional, Set

logger = logging.getLogger("isync.state")


class StateManager:
    """
    Tracks file state for one sync task.

    State file: .isync_state.json in the local sync directory.
    Runtime-only: _pending set to prevent re-syncing files
    that are currently being transferred.
    """

    def __init__(self, local_root: str, task_name: str):
        self.local_root = local_root
        self.task_name = task_name
        self._path = os.path.join(local_root, ".isync_state.json")
        self._lock = threading.Lock()
        self.data = self._load()
        # Runtime: files currently being transferred (not persisted)
        self._pending: Set[str] = set()
        # Transfer progress: {path: (done_bytes, total_bytes)}
        self._progress: Dict[str, tuple] = {}

    # ── pending (transfer-in-progress) tracking ───────────────────

    def mark_pending(self, path: str, total_bytes: int = 0):
        """Mark a file as being transferred. Sync ops skip pending files."""
        with self._lock:
            self._pending.add(path)
            if total_bytes > 0:
                self._progress[path] = (0, total_bytes)

    def update_progress(self, path: str, done_bytes: int):
        """Update transfer progress for a pending file."""
        with self._lock:
            if path in self._progress:
                _, total = self._progress[path]
                self._progress[path] = (done_bytes, total)

    def unmark_pending(self, path: str):
        """File transfer complete — remove from pending set."""
        with self._lock:
            self._pending.discard(path)
            self._progress.pop(path, None)

    def is_pending(self, path: str) -> bool:
        with self._lock:
            return path in self._pending

    def get_progress(self) -> Dict[str, tuple]:
        """Get all in-progress transfers {path: (done, total)}."""
        with self._lock:
            return dict(self._progress)

    # ── file state ───────────────────────────────────────────────

    @property
    def local_files(self) -> Dict[str, dict]:
        return self.data.get("local", {}).get("files", {})

    @property
    def remote_files(self) -> Dict[str, dict]:
        return self.data.get("remote", {}).get("files", {})

    # ── load / save ──────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            if os.path.isfile(self._path):
                with open(self._path, "r") as f:
                    data = json.load(f)
                if data.get("task") == self.task_name:
                    sc = data.get("sync_count", 0)
                    logger.info("状态已加载: local=%d remote=%d (第%d次同步)",
                                data.get("local", {}).get("file_count", 0),
                                data.get("remote", {}).get("file_count", 0), sc)
                    return data
        except Exception as e:
            logger.warning("无法加载状态: %s", e)
        return {"task": self.task_name, "updated": 0, "sync_count": 0,
                "local": {"file_count": 0, "files": {}},
                "remote": {"file_count": 0, "files": {}}}

    def save(self):
        try:
            self.data["updated"] = time.time()
            self.data["sync_count"] = self.data.get("sync_count", 0) + 1
            with open(self._path, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error("无法保存状态: %s", e)

    # ── snapshots ────────────────────────────────────────────────

    def set_local_snapshot(self, files: Dict[str, dict]):
        self.data["local"] = {
            "file_count": len(files),
            "files": {p: {"size": f["size"], "mtime": f["mtime"]}
                      for p, f in files.items()},
        }

    def set_remote_snapshot(self, files: Dict[str, dict]):
        self.data["remote"] = {
            "file_count": len(files),
            "files": {p: {"size": f["size"], "mtime": f["mtime"]}
                      for p, f in files.items()},
        }

    def update_local_file(self, path: str, size: int, mtime: float):
        self.data["local"]["files"][path] = {"size": size, "mtime": mtime}
        self.data["local"]["file_count"] = len(self.data["local"]["files"])

    def remove_local_file(self, path: str):
        self.data["local"]["files"].pop(path, None)
        self.data["local"]["file_count"] = len(self.data["local"]["files"])

    def update_remote_file(self, path: str, size: int, mtime: float):
        self.data["remote"]["files"][path] = {"size": size, "mtime": mtime}
        self.data["remote"]["file_count"] = len(self.data["remote"]["files"])

    def remove_remote_file(self, path: str):
        self.data["remote"]["files"].pop(path, None)
        self.data["remote"]["file_count"] = len(self.data["remote"]["files"])

    # ── diffs ────────────────────────────────────────────────────

    def diff_local(self, current_files: Dict[str, dict]) -> dict:
        """Compare current local files against stored state, skip pending."""
        stored = self.local_files
        current = set(current_files.keys())
        stored_set = set(stored.keys())
        added, modified, deleted = [], [], []
        for p in current - stored_set:
            if not self.is_pending(p):
                added.append(p)
        for p in current & stored_set:
            if self.is_pending(p):
                continue
            c, s = current_files[p], stored[p]
            if c["size"] != s["size"] or abs(c["mtime"] - s["mtime"]) > 2.0:
                modified.append(p)
        for p in stored_set - current:
            if not self.is_pending(p):
                deleted.append(p)
        return {"added": added, "modified": modified, "deleted": deleted}

    def diff_remote(self, current_files: Dict[str, dict]) -> dict:
        """Compare current remote files against stored state, skip pending."""
        stored = self.remote_files
        current = set(current_files.keys())
        stored_set = set(stored.keys())
        added, modified, deleted = [], [], []
        for p in current - stored_set:
            if not self.is_pending(p):
                added.append(p)
        for p in current & stored_set:
            if self.is_pending(p):
                continue
            c, s = current_files[p], stored[p]
            if c["size"] != s["size"] or abs(c["mtime"] - s["mtime"]) > 2.0:
                modified.append(p)
        for p in stored_set - current:
            if not self.is_pending(p):
                deleted.append(p)
        return {"added": added, "modified": modified, "deleted": deleted}
