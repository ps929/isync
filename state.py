"""
iSync — State Manager
Maintains a .isync_state.json in each sync directory that records
the last-known state of both local and remote files. Used for
precise incremental sync without relying on mtime comparisons.
"""

import os
import json
import time
import logging
from typing import Dict, Optional

logger = logging.getLogger("isync.state")


class StateManager:
    """
    Tracks file state for one sync task. The state file is stored
    in the local sync directory as .isync_state.json.

    State format:
    {
      "task": "my-sync",
      "updated": 1781434813.0,
      "local": {"file_count": N, "files": {path: {size, mtime}}},
      "remote": {"file_count": N, "files": {path: {size, mtime}}}
    }
    """

    def __init__(self, local_root: str, task_name: str):
        self.local_root = local_root
        self.task_name = task_name
        self._path = os.path.join(local_root, ".isync_state.json")
        self.data = self._load()

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
                    logger.info("State loaded: local=%d remote=%d files",
                                data.get("local", {}).get("file_count", 0),
                                data.get("remote", {}).get("file_count", 0))
                    return data
        except Exception as e:
            logger.warning("Cannot load state: %s", e)
        return {"task": self.task_name, "updated": 0,
                "local": {"file_count": 0, "files": {}},
                "remote": {"file_count": 0, "files": {}}}

    def save(self):
        """Persist current state to disk."""
        try:
            self.data["updated"] = time.time()
            with open(self._path, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error("Cannot save state: %s", e)

    # ── update helpers ───────────────────────────────────────────

    def set_local_snapshot(self, files: Dict[str, dict]):
        """Replace the local state with a full snapshot (after initial sync)."""
        self.data["local"] = {
            "file_count": len(files),
            "files": {p: {"size": f["size"], "mtime": f["mtime"]}
                      for p, f in files.items()},
        }

    def set_remote_snapshot(self, files: Dict[str, dict]):
        """Replace the remote state with a full snapshot."""
        self.data["remote"] = {
            "file_count": len(files),
            "files": {p: {"size": f["size"], "mtime": f["mtime"]}
                      for p, f in files.items()},
        }

    def update_local_file(self, path: str, size: int, mtime: float):
        """Update or add a single local file in the state."""
        self.data["local"]["files"][path] = {"size": size, "mtime": mtime}
        self.data["local"]["file_count"] = len(self.data["local"]["files"])

    def remove_local_file(self, path: str):
        """Remove a local file from the state (deleted)."""
        self.data["local"]["files"].pop(path, None)
        self.data["local"]["file_count"] = len(self.data["local"]["files"])

    def update_remote_file(self, path: str, size: int, mtime: float):
        """Update or add a single remote file in the state."""
        self.data["remote"]["files"][path] = {"size": size, "mtime": mtime}
        self.data["remote"]["file_count"] = len(self.data["remote"]["files"])

    def remove_remote_file(self, path: str):
        """Remove a remote file from the state (deleted)."""
        self.data["remote"]["files"].pop(path, None)
        self.data["remote"]["file_count"] = len(self.data["remote"]["files"])

    # ── diff helpers ─────────────────────────────────────────────

    def diff_local(self, current_files: Dict[str, dict]) -> dict:
        """
        Compare current local files against stored local state.
        Returns {added: [path], modified: [path], deleted: [path]}
        """
        stored = self.local_files
        current_paths = set(current_files.keys())
        stored_paths = set(stored.keys())

        added = []
        modified = []
        for p in current_paths - stored_paths:
            added.append(p)
        for p in current_paths & stored_paths:
            c = current_files[p]
            s = stored[p]
            if c["size"] != s["size"] or abs(c["mtime"] - s["mtime"]) > 2.0:
                modified.append(p)
        deleted = list(stored_paths - current_paths)

        return {"added": added, "modified": modified, "deleted": deleted}

    def diff_remote(self, current_files: Dict[str, dict]) -> dict:
        """
        Compare current remote files against stored remote state.
        Returns {added: [path], modified: [path], deleted: [path]}
        """
        stored = self.remote_files
        current_paths = set(current_files.keys())
        stored_paths = set(stored.keys())

        added = []
        modified = []
        for p in current_paths - stored_paths:
            added.append(p)
        for p in current_paths & stored_paths:
            c = current_files[p]
            s = stored[p]
            if c["size"] != s["size"] or abs(c["mtime"] - s["mtime"]) > 2.0:
                modified.append(p)
        deleted = list(stored_paths - current_paths)

        return {"added": added, "modified": modified, "deleted": deleted}
