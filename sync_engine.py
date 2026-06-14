"""
iSync - Synchronization engine
Core sync logic: scan, diff, resolve conflicts, and execute transfers.
"""

import os
import json
import time
import fnmatch
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from config import SyncTask
from sftp_client import SFTPClient, FileInfo, TransferError

logger = logging.getLogger("isync.engine")


@dataclass
class SyncPlan:
    """Describes what needs to happen for a sync to complete."""
    to_upload: List[str] = field(default_factory=list)        # local -> remote
    to_download: List[str] = field(default_factory=list)      # remote -> local
    to_delete_local: List[str] = field(default_factory=list)  # delete on local
    to_delete_remote: List[str] = field(default_factory=list) # delete on remote
    skipped: List[str] = field(default_factory=list)          # unchanged
    conflicts_resolved: List[str] = field(default_factory=list)  # conflicts handled

    @property
    def total_actions(self) -> int:
        return (len(self.to_upload) + len(self.to_download) +
                len(self.to_delete_local) + len(self.to_delete_remote))

    @property
    def is_empty(self) -> bool:
        return self.total_actions == 0


@dataclass
class SyncRecord:
    """Structured record of a single sync operation for audit/logging."""
    timestamp: str = ""             # ISO 8601
    task_name: str = ""
    direction: str = ""
    conflict_resolution: str = ""
    duration_seconds: float = 0.0
    uploaded: List[str] = field(default_factory=list)
    downloaded: List[str] = field(default_factory=list)
    deleted_local: List[str] = field(default_factory=list)
    deleted_remote: List[str] = field(default_factory=list)
    skipped_count: int = 0
    errors: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "task_name": self.task_name,
            "direction": self.direction,
            "conflict_resolution": self.conflict_resolution,
            "duration_seconds": round(self.duration_seconds, 2),
            "summary": {
                "uploaded": len(self.uploaded),
                "downloaded": len(self.downloaded),
                "deleted_local": len(self.deleted_local),
                "deleted_remote": len(self.deleted_remote),
                "skipped": self.skipped_count,
                "errors": len(self.errors),
            },
            "uploaded": self.uploaded,
            "downloaded": self.downloaded,
            "deleted_local": self.deleted_local,
            "deleted_remote": self.deleted_remote,
            "errors": self.errors,
        }


class SyncEngine:
    """
    Core sync engine that compares local and remote file trees
    and executes the necessary transfers.
    """

    def __init__(self, task: SyncTask, sftp: SFTPClient, max_clock_skew: int = 300,
                 sync_log_dir: str = "", sync_log_max_files: int = 500,
                 sync_log_max_days: int = 30, display=None):
        self.task = task
        self.sftp = sftp
        self.max_clock_skew = max_clock_skew
        self.sync_log_dir = os.path.expanduser(sync_log_dir) if sync_log_dir else ""
        self.sync_log_max_files = sync_log_max_files
        self.sync_log_max_days = sync_log_max_days
        self.display = display  # SyncDisplay or None

    # ── scanning ──────────────────────────────────────────────────

    def scan_local(self) -> Dict[str, FileInfo]:
        """
        Recursively scan the local sync directory.
        If comparison='content', computes quick_hash for each file.
        """
        result: Dict[str, FileInfo] = {}
        local_root = self.task.local_path
        use_hash = self.task.comparison == "content"

        if not os.path.isdir(local_root):
            logger.warning("Local path does not exist: %s", local_root)
            return result

        for dirpath, dirnames, filenames in os.walk(local_root):
            dirnames[:] = [
                d for d in dirnames
                if not self._is_excluded(os.path.relpath(os.path.join(dirpath, d), local_root))
            ]

            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, local_root)

                if self._is_excluded(rel):
                    logger.debug("Excluding local: %s", rel)
                    continue

                s = os.stat(full)
                qh = None
                if use_hash:
                    qh = SFTPClient.compute_local_hash(full)
                result[rel] = FileInfo(
                    path=rel, size=s.st_size, mtime=s.st_mtime,
                    quick_hash=qh,
                )

        logger.info("Scanned local: %d files%s in %s",
                     len(result),
                     " (content hashed)" if use_hash else "",
                     local_root)
        return result

    def scan_remote(self) -> Dict[str, FileInfo]:
        """
        Scan the remote sync directory via SFTP.
        If comparison='content', computes quick_hash for each file
        via partial SFTP reads (head + tail chunks only).
        """
        result = self.sftp.list_files(self.task.remote_path,
                                      exclude=self.task.exclude)
        use_hash = self.task.comparison == "content"

        if use_hash:
            remote_root = self.task.remote_path.rstrip("/")
            hashed = 0
            for rel_path in list(result.keys()):
                remote_path = f"{remote_root}/{rel_path}"
                try:
                    qh = self.sftp.hash_chunks(remote_path)
                    fi = result[rel_path]
                    result[rel_path] = FileInfo(
                        path=fi.path, size=fi.size, mtime=fi.mtime,
                        quick_hash=qh,
                    )
                    hashed += 1
                except Exception as e:
                    logger.debug("Skip hash for remote %s: %s", rel_path, e)

        logger.info("Scanned remote: %d files%s in %s",
                     len(result),
                     " (content hashed)" if use_hash else "",
                     self.task.remote_path)
        return result

    # ── diff ──────────────────────────────────────────────────────

    def diff(self, local_files: Dict[str, FileInfo],
             remote_files: Dict[str, FileInfo]) -> SyncPlan:
        """
        Compare local and remote file trees and produce a SyncPlan.
        """
        plan = SyncPlan()
        all_paths = set(local_files.keys()) | set(remote_files.keys())
        direction = self.task.direction
        delete_propagate = self.task.delete_propagate

        for path in all_paths:
            in_local = path in local_files
            in_remote = path in remote_files

            if in_local and in_remote:
                # File exists on both sides — compare
                if local_files[path] == remote_files[path]:
                    plan.skipped.append(path)
                else:
                    # Different — this is a conflict to resolve
                    self._classify_conflict(path, local_files[path],
                                            remote_files[path], plan)

            elif in_local and not in_remote:
                # Only on local side
                if direction == "remote-to-local":
                    # Remote is source — local-only files are "extra" on target
                    if delete_propagate:
                        plan.to_delete_local.append(path)
                    else:
                        plan.skipped.append(path)  # ignore extra file on target
                else:
                    # bidirectional or local-to-remote: upload to remote
                    plan.to_upload.append(path)

            elif in_remote and not in_local:
                # Only on remote side
                if direction == "local-to-remote":
                    # Local is source — remote-only files are "extra" on target
                    if delete_propagate:
                        plan.to_delete_remote.append(path)
                    else:
                        plan.skipped.append(path)  # ignore extra file on target
                else:
                    # bidirectional or remote-to-local: download to local
                    plan.to_download.append(path)

        return plan

    def _classify_conflict(self, path: str, local_info: FileInfo,
                           remote_info: FileInfo, plan: SyncPlan):
        """Determine how to resolve a file that exists on both sides but differs."""
        resolution = self.task.conflict_resolution
        direction = self.task.direction

        if resolution == "newer":
            # Use mtime to decide which is newer
            if local_info.mtime >= remote_info.mtime:
                winner = "local"
            else:
                winner = "remote"
        elif resolution == "local":
            winner = "local"
        elif resolution == "remote":
            winner = "remote"
        else:
            winner = "newer"  # fallback

        # Direction may override the resolution
        if direction == "local-to-remote":
            winner = "local"
        elif direction == "remote-to-local":
            winner = "remote"

        if winner == "local":
            plan.to_upload.append(path)
        else:
            plan.to_download.append(path)

        plan.conflicts_resolved.append(path)

        # Warn if clock skew is suspiciously large
        time_diff = abs(local_info.mtime - remote_info.mtime)
        if time_diff > self.max_clock_skew:
            logger.warning(
                "Clock skew detected: '%s' mtime differs by %.0fs "
                "(local=%.0f, remote=%.0f). "
                "Check NTP/time sync on both machines — "
                "conflict resolution may be unreliable.",
                path, time_diff, local_info.mtime, remote_info.mtime,
            )

    # ── execute ───────────────────────────────────────────────────

    def execute(self, plan: SyncPlan) -> tuple:
        """
        Execute a sync plan.
        Returns (stats_dict, record_lists) where record_lists contains
        per-file paths for structured logging.
        """
        uploaded, downloaded = [], []
        deleted_local, deleted_remote = [], []
        errors = []
        skipped_count = len(plan.skipped)

        local_root = self.task.local_path
        remote_root = self.task.remote_path.rstrip("/")

        if plan.is_empty:
            logger.info("Nothing to sync — files are in sync.")
            return ({"uploaded": 0, "downloaded": 0, "deleted_local": 0,
                     "deleted_remote": 0, "skipped": skipped_count, "errors": 0},
                    uploaded, downloaded, deleted_local, deleted_remote, errors)

        logger.info("Sync plan: ↑%d uploads, ↓%d downloads, "
                     "✗local:%d, ✗remote:%d",
                     len(plan.to_upload), len(plan.to_download),
                     len(plan.to_delete_local), len(plan.to_delete_remote))

        # Init display
        total_actions = plan.total_actions
        if self.display:
            self.display.set_total_files(total_actions)
            self.display.on_skip(skipped_count)

        # Uploads
        for rel in plan.to_upload:
            local_path = os.path.join(local_root, rel)
            remote_path = f"{remote_root}/{rel}"
            try:
                size = os.path.getsize(local_path)
                if self.display:
                    self.display.on_upload_start(rel, size)
                cb = None
                if self.display:
                    cb = lambda done, total, p=rel: self.display.on_upload_progress(p, done, total)
                self.sftp.upload(local_path, remote_path, callback=cb)
                uploaded.append(rel)
                if self.display:
                    self.display.on_upload_done(rel)
                logger.info("↑ %s", rel)
            except Exception as e:
                errors.append({"path": rel, "operation": "upload", "error": str(e)})
                if self.display:
                    self.display.on_error(rel, str(e))
                logger.error("Upload failed: %s — %s", rel, e)

        # Downloads
        for rel in plan.to_download:
            local_path = os.path.join(local_root, rel)
            remote_path = f"{remote_root}/{rel}"
            try:
                # Get remote file size for progress
                info = self.sftp.get_info(remote_path)
                size = info.size if info else 0
                if self.display:
                    self.display.on_download_start(rel, size)
                cb = None
                if self.display:
                    cb = lambda done, total, p=rel: self.display.on_download_progress(p, done, total)
                self.sftp.download(remote_path, local_path, callback=cb)
                downloaded.append(rel)
                if self.display:
                    self.display.on_download_done(rel)
                logger.info("↓ %s", rel)
            except Exception as e:
                errors.append({"path": rel, "operation": "download", "error": str(e)})
                if self.display:
                    self.display.on_error(rel, str(e))
                logger.error("Download failed: %s — %s", rel, e)

        # Delete remote
        for rel in plan.to_delete_remote:
            remote_path = f"{remote_root}/{rel}"
            try:
                self.sftp.delete(remote_path)
                deleted_remote.append(rel)
                if self.display:
                    self.display.on_delete(rel, "remote")
                logger.info("✗ remote: %s", rel)
            except Exception as e:
                errors.append({"path": rel, "operation": "delete_remote", "error": str(e)})
                if self.display:
                    self.display.on_error(rel, str(e))
                logger.error("Remote delete failed: %s — %s", rel, e)

        # Delete local
        for rel in plan.to_delete_local:
            local_path = os.path.join(local_root, rel)
            try:
                os.remove(local_path)
                deleted_local.append(rel)
                if self.display:
                    self.display.on_delete(rel, "local")
                logger.info("✗ local: %s", rel)
            except FileNotFoundError:
                logger.debug("Local file already gone: %s", rel)
            except Exception as e:
                errors.append({"path": rel, "operation": "delete_local", "error": str(e)})
                if self.display:
                    self.display.on_error(rel, str(e))
                logger.error("Local delete failed: %s — %s", rel, e)

        stats = {
            "uploaded": len(uploaded),
            "downloaded": len(downloaded),
            "deleted_local": len(deleted_local),
            "deleted_remote": len(deleted_remote),
            "skipped": skipped_count,
            "errors": len(errors),
        }
        return (stats, uploaded, downloaded, deleted_local, deleted_remote, errors)

    # ── full sync ─────────────────────────────────────────────────

    def sync(self) -> Dict[str, int]:
        """
        Run a complete sync: scan -> diff -> execute.
        If sync_log_dir is configured, saves a structured JSON record.
        """
        t0 = time.monotonic()
        logger.info("=== Sync started: %s ===", self.task.name)
        logger.info("Direction: %s | Conflict: %s | Delete propagate: %s",
                     self.task.direction, self.task.conflict_resolution,
                     self.task.delete_propagate)

        local_files = self.scan_local()
        remote_files = self.scan_remote()

        plan = self.diff(local_files, remote_files)
        stats, uploaded, downloaded, del_local, del_remote, errors = \
            self.execute(plan)

        elapsed = time.monotonic() - t0

        # Build and save structured record
        record = SyncRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            task_name=self.task.name,
            direction=self.task.direction,
            conflict_resolution=self.task.conflict_resolution,
            duration_seconds=elapsed,
            uploaded=uploaded,
            downloaded=downloaded,
            deleted_local=del_local,
            deleted_remote=del_remote,
            skipped_count=stats["skipped"],
            errors=errors,
        )
        self._save_record(record)

        logger.info("=== Sync complete: ↑%d ↓%d ✗L:%d ✗R:%d skip:%d err:%d ===",
                     stats["uploaded"], stats["downloaded"],
                     stats["deleted_local"], stats["deleted_remote"],
                     stats["skipped"], stats["errors"])

        return stats

    def _save_record(self, record: SyncRecord):
        """Write structured sync record to JSON file and rotate old records."""
        if not self.sync_log_dir:
            return
        try:
            os.makedirs(self.sync_log_dir, exist_ok=True)

            # filename: task_name-YYYYMMDD-HHMMSS-ffffff.json (microsecond precision)
            safe_name = self.task.name.replace("/", "_").replace(" ", "_")
            ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            filename = f"{safe_name}-{ts}.json"
            filepath = os.path.join(self.sync_log_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, indent=2, ensure_ascii=False)
            logger.info("Sync record saved: %s", filepath)

            # Rotate: delete records exceeding max_files or max_days
            self._rotate_records()
        except Exception as e:
            logger.error("Failed to save sync record: %s", e)

    def _rotate_records(self):
        """
        Remove old sync records that exceed max_files (per task)
        or max_days retention limits.
        """
        try:
            safe_name = self.task.name.replace("/", "_").replace(" ", "_")
            # Gather records for this task
            records = []
            for fname in os.listdir(self.sync_log_dir):
                if fname.startswith(safe_name + "-") and fname.endswith(".json"):
                    fpath = os.path.join(self.sync_log_dir, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        mtime = 0
                    records.append((mtime, fpath))

            if not records:
                return

            # Sort by mtime ascending (oldest first)
            records.sort()

            # 1) Age-based cleanup
            if self.sync_log_max_days > 0:
                cutoff = time.time() - (self.sync_log_max_days * 86400)
                for mtime, fpath in records:
                    if mtime < cutoff:
                        os.remove(fpath)
                        logger.debug("Rotated (age): %s", os.path.basename(fpath))

            # 2) Count-based cleanup — keep only N newest
            remaining = []
            for fname in os.listdir(self.sync_log_dir):
                if fname.startswith(safe_name + "-") and fname.endswith(".json"):
                    fpath = os.path.join(self.sync_log_dir, fname)
                    if os.path.isfile(fpath):
                        remaining.append(fpath)

            # Re-sort by mtime descending (newest first)
            remaining.sort(key=lambda p: os.path.getmtime(p), reverse=True)

            if self.sync_log_max_files > 0 and len(remaining) > self.sync_log_max_files:
                for fpath in remaining[self.sync_log_max_files:]:
                    os.remove(fpath)
                    logger.debug("Rotated (count): %s", os.path.basename(fpath))

        except Exception as e:
            logger.debug("Record rotation skipped: %s", e)

    def sync_single(self, rel_path: str, event_type: str):
        """
        Handle a single file change for incremental (watch-driven) sync.

        Before blindly uploading/deleting, checks the remote file state
        to detect simultaneous edits on both sides. When both sides
        changed the same file, applies conflict_resolution.
        """
        local_path = os.path.join(self.task.local_path, rel_path)
        remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"
        direction = self.task.direction
        resolution = self.task.conflict_resolution

        if event_type in ("created", "modified"):
            if not os.path.isfile(local_path):
                return

            # Wait for file to stop being written (large save / streaming)
            self._wait_until_stable(local_path)

            # Check remote state before overwriting
            remote_info = self.sftp.get_info(remote_path)
            if remote_info is not None:
                local_stat = os.stat(local_path)
                local_info = FileInfo(
                    path=rel_path, size=local_stat.st_size,
                    mtime=local_stat.st_mtime,
                )

                if local_info == remote_info:
                    logger.debug("Already in sync: %s", rel_path)
                    return

                # Both sides differ — check if remote was independently modified
                if direction == "remote-to-local":
                    # Remote is source of truth → download instead of upload
                    action = "download"
                elif direction == "local-to-remote":
                    action = "upload"
                elif resolution == "remote":
                    action = "download"
                elif resolution == "local":
                    action = "upload"
                elif resolution == "newer":
                    if remote_info.mtime > local_info.mtime:
                        action = "download"
                    else:
                        action = "upload"
                else:
                    action = "upload"

                if action == "download":
                    try:
                        self.sftp.download(remote_path, local_path)
                        logger.info("↓ [watch] %s (remote won conflict)", rel_path)
                    except Exception as e:
                        logger.error("Conflict download failed: %s — %s", rel_path, e)
                    return

            # Safe to upload
            try:
                size = os.path.getsize(local_path)
                if self.display:
                    self.display.on_upload_start(rel_path, size)
                self.sftp.upload(local_path, remote_path)
                if self.display:
                    self.display.on_upload_done(rel_path)
                logger.info("↑ [watch] %s", rel_path)
            except Exception as e:
                logger.error("Watch upload failed: %s — %s", rel_path, e)

        elif event_type == "deleted":
            if not self.task.delete_propagate:
                return

            # Heuristic: if remote file was modified very recently,
            # it may have been an independent edit — skip deletion.
            # If the remote mtime is older, the file was likely last
            # synced by us and is safe to delete.
            if direction != "local-to-remote":
                remote_info = self.sftp.get_info(remote_path)
                if remote_info is not None:
                    age = time.time() - remote_info.mtime
                    grace = max(self.task.poll_interval * 2, 5)
                    if age < grace:
                        logger.warning(
                            "Not deleting remote %s — file modified %.0fs ago "
                            "(within %ds grace period). May have been edited remotely.",
                            rel_path, age, grace,
                        )
                        return

            try:
                self.sftp.delete(remote_path)
                logger.info("✗ [watch] remote: %s", rel_path)
            except Exception as e:
                logger.error("Watch delete failed: %s — %s", rel_path, e)

    # ── remote poll helpers ────────────────────────────────────────

    def resolve_remote_change(self, rel_path: str, remote_info: FileInfo) -> str:
        """
        Decide what to do when a remote file changed during polling.
        Returns 'download', 'skip', or 'delete_local'.
        Considers conflict_resolution and current local state.
        """
        local_root = self.task.local_path
        local_path = os.path.join(local_root, rel_path)
        direction = self.task.direction
        resolution = self.task.conflict_resolution

        # local-to-remote: remote changes are ignored (local is source of truth)
        if direction == "local-to-remote":
            return "skip"

        # File doesn't exist locally → definitely download
        if not os.path.isfile(local_path):
            return "download"

        # File exists locally — check if it differs from remote
        local_stat = os.stat(local_path)
        local_info = FileInfo(
            path=rel_path, size=local_stat.st_size,
            mtime=local_stat.st_mtime,
        )

        if local_info == remote_info:
            return "skip"  # already in sync (shouldn't happen in poll, but safe)

        # Different — resolve
        time_diff = abs(local_info.mtime - remote_info.mtime)
        if time_diff > self.max_clock_skew:
            logger.warning(
                "Clock skew detected: '%s' mtime differs by %.0fs "
                "(local=%.0f, remote=%.0f).",
                rel_path, time_diff, local_info.mtime, remote_info.mtime,
            )

        if direction == "remote-to-local":
            # Remote always wins
            return "download"

        # Bidirectional — apply conflict resolution
        if resolution == "remote":
            return "download"
        elif resolution == "local":
            return "skip"
        elif resolution == "newer":
            if remote_info.mtime > local_info.mtime:
                return "download"
            else:
                return "skip"

        return "skip"

    def download_file(self, rel_path: str):
        """Download a single file from remote to local."""
        local_path = os.path.join(self.task.local_path, rel_path)
        remote_path = f"{self.task.remote_path.rstrip('/')}/{rel_path}"
        # Display
        info = self.sftp.get_info(remote_path)
        size = info.size if info else 0
        if self.display:
            self.display.on_download_start(rel_path, size)
        self.sftp.download(remote_path, local_path)
        if self.display:
            self.display.on_download_done(rel_path)

    def delete_local_file(self, rel_path: str):
        """Delete a single local file (called when remote deleted it)."""
        local_path = os.path.join(self.task.local_path, rel_path)
        try:
            os.remove(local_path)
            if self.display:
                self.display.on_delete(rel_path, "local")
            logger.info("✗ [poll] local: %s", rel_path)
        except FileNotFoundError:
            pass

    # ── file write stabilization ───────────────────────────────────

    def _wait_until_stable(self, local_path: str,
                           check_interval: float = 0.3,
                           max_wait: float = 5.0) -> bool:
        """
        Wait until the file stops changing size (write completion detection).

        When an editor saves a large file (or a tool streams data to disk),
        the file is written over a period of time. If we upload mid-write,
        the remote side gets a partial/corrupt file.

        This method polls the file size until it stabilizes for one full
        check interval, or gives up after max_wait seconds.

        Returns True if the file stabilized, False if it timed out.
        """
        waited = 0.0
        last_size = -1

        while waited < max_wait:
            try:
                current_size = os.path.getsize(local_path)
            except OSError:
                return False  # file vanished or inaccessible

            if current_size == last_size and last_size >= 0:
                return True  # size unchanged for one full cycle

            last_size = current_size
            time.sleep(check_interval)
            waited += check_interval

        # Timed out — file is still being written. Upload anyway but warn.
        if last_size >= 0:
            logger.warning(
                "File still being written after %.1fs: %s — "
                "uploading anyway, may be incomplete.",
                max_wait, os.path.basename(local_path),
            )
        return False

    # ── helpers ───────────────────────────────────────────────────

    def _is_excluded(self, rel_path: str) -> bool:
        """Check if a relative path should be excluded."""
        for pat in self.task.exclude:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            if fnmatch.fnmatch(os.path.basename(rel_path), pat):
                return True
        return False
