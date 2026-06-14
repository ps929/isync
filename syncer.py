"""iSync — Sync orchestrator. Coordinates index, scanner, and SFTP."""
import os, shutil, time, logging
from typing import Dict
from config import TaskConfig
from sftp_client import SFTPClient
from scanner import scan_local
from index_db import IndexDB, compute_blocks

logger = logging.getLogger("isync.syncer")

class Syncer:
    """High-level sync controller.

    Flow:
      1. Load local index from SQLite
      2. Scan local filesystem → update local index
      3. Connect remote via SFTP → load remote index
      4. Compare indices → determine what to sync
      5. Transfer files → update indices
    """

    def __init__(self, task: TaskConfig, sftp: SFTPClient):
        self.task = task
        self.sftp = sftp
        self.db = IndexDB(os.path.join(task.local_path, ".isync.db"))

    # ── initial sync ──────────────────────────────────────────────

    def initial_sync(self):
        """Full scan + index build + transfer. Called once on startup."""
        logger.info("=== 初始同步: %s ===", self.task.name)

        # Scan local
        logger.info("扫描本地...")
        local, local_dirs = scan_local(self.task.local_path, self.task.exclude,
                                       self.task.block_size)
        self.db.set_files("local", local)
        logger.info("  本地: %d 文件, %d 目录", len(local), len(local_dirs))

        # Scan remote
        logger.info("扫描远端...")
        remote_raw, remote_dirs = self.sftp.list_files(
            self.task.remote_path, self.task.exclude)
        remote = {p: {"size": f.size, "mtime": f.mtime, "block_hash": "", "block_count": 0}
                  for p, f in remote_raw.items()}
        self.db.set_files("remote", remote)
        logger.info("  远端: %d 文件, %d 目录", len(remote), len(remote_dirs))

        # Create missing directories
        self._sync_dirs(local_dirs, remote_dirs)

        # Diff & transfer
        diff = self.db.diff("local", "remote")
        self._apply_diff(diff, local, remote)
        logger.info("初始同步完成")

    # ── incremental ───────────────────────────────────────────────

    def sync_local_change(self, rel_path: str, event_type: str):
        """Handle a local file change from the watcher."""
        if event_type in ("created", "modified"):
            if self.task.direction == "remote-to-local":
                return  # local is not the source of truth
            local_path = os.path.join(self.task.local_path, rel_path)
            if not os.path.isfile(local_path):
                return
            st = os.stat(local_path)
            hashes, combined = compute_blocks(local_path, self.task.block_size)
            info = {"size": st.st_size, "mtime": st.st_mtime,
                    "block_hash": combined, "block_count": len(hashes)}
            old = self.db.get_file("local", rel_path)

            if old and old["block_hash"] == combined:
                return  # unchanged

            # Upload
            self.db.update_file("local", rel_path, info)
            remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"
            try:
                self.sftp.upload(local_path, remote_path)
                self.db.update_file("remote", rel_path, info)
                logger.info("↑ %s", rel_path)
            except Exception as e:
                logger.error("Upload failed: %s — %s", rel_path, e)

        elif event_type == "deleted" and self.task.delete_propagate:
            local_path = os.path.join(self.task.local_path, rel_path)
            # Check if it was a directory (no longer exists locally)
            old_file = self.db.get_file("local", rel_path)
            if old_file:
                # File deletion
                remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"
                try:
                    self.sftp.delete(remote_path)
                    self.db.remove_file("local", rel_path)
                    self.db.remove_file("remote", rel_path)
                    logger.info("✗ %s", rel_path)
                except Exception as e:
                    logger.error("Delete failed: %s — %s", rel_path, e)
            else:
                # Directory deletion — delete on remote too
                remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"
                try:
                    # Remove remote dir contents + dir itself via SFTP
                    self._delete_remote_dir(remote_path)
                    logger.info("✗ 目录 %s", rel_path)
                except Exception as e:
                    logger.error("Delete dir failed: %s — %s", rel_path, e)

    def poll_remote(self):
        """Scan remote, compare with index, download changes + handle dir deletions."""
        remote_raw, remote_dirs = self.sftp.list_files(
            self.task.remote_path, self.task.exclude)
        remote = {p: {"size": f.size, "mtime": f.mtime, "block_hash": "", "block_count": 0}
                  for p, f in remote_raw.items()}
        self.db.set_files("remote", remote)
        # Sync directories: create new, delete gone
        _, local_dirs = scan_local(self.task.local_path, self.task.exclude,
                                   self.task.block_size)
        self._create_missing_local_dirs(remote_dirs, local_dirs)
        self._delete_gone_local_dirs(remote_dirs, local_dirs)
        diff = self.db.diff("remote", "local")
        self._apply_remote_diff(diff, remote)

    # ── internal ──────────────────────────────────────────────────

    def _apply_diff(self, diff: dict, local: dict, remote: dict):
        """
        Apply initial diff (local vs remote).
        diff["added"]: local has, remote doesn't → upload
        diff["deleted"]: remote has, local doesn't → download
        diff["modified"]: both have, differ → conflict resolution
        """
        # New local files → upload to remote
        for p in diff["added"]:
            if self.task.direction != "remote-to-local":
                self._upload_one(p, local[p])
        # Remote-only files → download to local
        for p in diff["deleted"]:
            if self.task.direction != "local-to-remote":
                self._download_one(p, remote[p])
        # Both sides have, content differs
        for p in diff["modified"]:
            if self.task.direction == "remote-to-local":
                self._download_one(p, remote[p])
            elif self.task.direction == "local-to-remote":
                self._upload_one(p, local[p])
            elif self.task.conflict_resolution == "local":
                self._upload_one(p, local[p])
            elif self.task.conflict_resolution == "remote":
                self._download_one(p, remote[p])
            else:  # newer
                if local[p]["mtime"] >= remote[p]["mtime"]:
                    self._upload_one(p, local[p])
                else:
                    self._download_one(p, remote[p])

    def _apply_remote_diff(self, diff: dict, remote: dict):
        """
        Apply remote-side diff.
        diff["added"]: new on remote → download
        diff["deleted"]: gone from remote → delete local (if propagate)
        diff["modified"]: changed on remote → download (if newer/local policy)
        """
        for p in diff["added"]:
            if self.task.direction != "local-to-remote":
                self._download_one(p, remote[p])
        for p in diff["modified"]:
            if self.task.direction == "local-to-remote":
                continue
            if self.task.direction == "remote-to-local" or self.task.conflict_resolution == "remote":
                self._download_one(p, remote[p])
            elif self.task.conflict_resolution == "newer":
                local_info = self.db.get_file("local", p)
                if not local_info or remote[p]["mtime"] > local_info["mtime"]:
                    self._download_one(p, remote[p])
        for p in diff["deleted"]:
            if self.task.delete_propagate and self.task.direction != "local-to-remote":
                lp = os.path.join(self.task.local_path, p)
                try:
                    if os.path.isfile(lp): os.remove(lp)
                    self.db.remove_file("local", p)
                    logger.info("✗ %s (远端已删除)", p)
                except Exception as e:
                    logger.error("Delete local failed: %s", e)

    def _sync_dirs(self, local_dirs: set, remote_dirs: set):
        """Create missing directories on both sides."""
        for d in local_dirs - remote_dirs:
            if self.task.direction != "remote-to-local":
                try:
                    self.sftp.mkdir(f"{self.task.remote_path.rstrip('/')}/{d}")
                    logger.debug("Created remote dir: %s", d)
                except Exception as e:
                    logger.debug("Mkdir remote %s: %s", d, e)
        for d in remote_dirs - local_dirs:
            if self.task.direction != "local-to-remote":
                try:
                    os.makedirs(os.path.join(self.task.local_path, d), exist_ok=True)
                    logger.debug("Created local dir: %s", d)
                except Exception as e:
                    logger.debug("Mkdir local %s: %s", d, e)

    def _create_missing_local_dirs(self, remote_dirs: set, local_dirs: set):
        """Create remote-only directories locally."""
        for d in remote_dirs - local_dirs:
            if self.task.direction != "local-to-remote":
                try:
                    os.makedirs(os.path.join(self.task.local_path, d), exist_ok=True)
                except Exception as e:
                    logger.debug("Mkdir %s: %s", d, e)

    def _delete_gone_local_dirs(self, remote_dirs: set, local_dirs: set):
        """Delete local directories that no longer exist on remote."""
        if not self.task.delete_propagate:
            return
        if self.task.direction == "local-to-remote":
            return
        # Deepest first so children are removed before parents
        gone = local_dirs - remote_dirs
        for d in sorted(gone, key=lambda x: -x.count('/')):
            try:
                lp = os.path.join(self.task.local_path, d)
                if os.path.isdir(lp):
                    shutil.rmtree(lp)
                    logger.info("✗ 目录 %s (远端已删除)", d)
            except Exception as e:
                logger.debug("Rmdir %s: %s", d, e)

    def _delete_remote_dir(self, remote_path: str):
        """Recursively delete a remote directory and its contents."""
        try:
            for entry in self.sftp._sftp.listdir_attr(remote_path):
                full = f"{remote_path}/{entry.filename}"
                import stat as st_mod
                if st_mod.S_ISDIR(entry.st_mode):
                    self._delete_remote_dir(full)
                else:
                    self.sftp.delete(full)
            self.sftp._sftp.rmdir(remote_path)
        except FileNotFoundError:
            pass

    def _upload_one(self, rel: str, info: dict):
        lp = os.path.join(self.task.local_path, rel)
        rp = f"{self.task.remote_path.rstrip('/')}/{rel}"
        try:
            self.sftp.upload(lp, rp)
            self.db.update_file("remote", rel, info)
            logger.info("↑ %s", rel)
        except Exception as e:
            logger.error("Upload %s: %s", rel, e)

    def _download_one(self, rel: str, info: dict):
        lp = os.path.join(self.task.local_path, rel)
        rp = f"{self.task.remote_path.rstrip('/')}/{rel}"
        try:
            self.sftp.download(rp, lp)
            self.db.update_file("local", rel, info)
            # Re-compute block hash
            if os.path.isfile(lp) and info.get("size", 0) > 0:
                hashes, combined = compute_blocks(lp, self.task.block_size)
                info["block_hash"] = combined
                info["block_count"] = len(hashes)
                self.db.update_file("local", rel, info)
            logger.info("↓ %s", rel)
        except Exception as e:
            logger.error("Download %s: %s", rel, e)

    def close(self):
        self.db.close()
