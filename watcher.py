"""
iSync - File system watcher
Monitors local directory changes and triggers incremental sync.
Built on watchdog.
"""

import os
import time
import fnmatch
import logging
import threading
from typing import Dict, Set, Optional, Callable

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from config import SyncTask
from sftp_client import FileInfo

logger = logging.getLogger("isync.watcher")


class DebouncedHandler(FileSystemEventHandler):
    """
    Watchdog event handler with debounce support.
    Groups rapid-fire events within a time window and triggers
    a single callback per unique file path.
    """

    def __init__(self, task: SyncTask, callback: Callable[[str, str], None],
                 debounce_seconds: float = 2.0):
        super().__init__()
        self.task = task
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._pending: Dict[str, str] = {}  # path -> event_type
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._enqueue(event.src_path, "created")

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._enqueue(event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            self._enqueue(event.src_path, "deleted")

    def on_moved(self, event: FileSystemEvent):
        # Treat move as: delete from old location, create at new location
        if not event.is_directory:
            self._enqueue(event.src_path, "deleted")
            self._enqueue(event.dest_path, "created")

    def _enqueue(self, abs_path: str, event_type: str):
        """Add an event to the pending queue and reset the debounce timer."""
        local_root = self.task.local_path
        try:
            rel_path = os.path.relpath(abs_path, local_root)
        except ValueError:
            # Path not under the watched root — ignore
            return

        # Check exclusion
        if self._is_excluded(rel_path):
            logger.debug("Watcher excluding: %s", rel_path)
            return

        with self._lock:
            # If the same path was deleted and then created again, keep the
            # latest event
            self._pending[rel_path] = event_type

            # Reset debounce timer
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._flush)
            self._timer.start()

            logger.debug("Event queued: %s -> %s", rel_path, event_type)

    def _flush(self):
        """Fire callbacks for all pending (debounced) events."""
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None

        for rel_path, event_type in pending.items():
            try:
                self.callback(rel_path, event_type)
            except Exception as e:
                logger.error("Callback error for %s: %s", rel_path, e)

    def _is_excluded(self, rel_path: str) -> bool:
        """Check if the path should be excluded from watching."""
        for pat in self.task.exclude:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            if fnmatch.fnmatch(os.path.basename(rel_path), pat):
                return True
        return False

    def flush_sync(self):
        """Flush any pending events (called before watcher stop)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._flush()


class FileWatcher:
    """
    Watches a local directory for changes and triggers sync callbacks.
    Runs the watchdog Observer in a background thread.
    """

    def __init__(self, task: SyncTask, on_change: Callable[[str, str], None]):
        self.task = task
        self.handler = DebouncedHandler(task, on_change, debounce_seconds=2.0)
        self._observer = Observer()
        self._running = False

    def start(self):
        """Start watching the local path."""
        local_path = self.task.local_path
        if not os.path.isdir(local_path):
            logger.warning("Local path not found, cannot watch: %s", local_path)
            return

        self._observer.schedule(self.handler, local_path, recursive=True)
        self._observer.start()
        self._running = True
        logger.info("Watching for changes: %s", local_path)

    def stop(self):
        """Stop watching and flush pending events."""
        if not self._running:
            return

        logger.info("Stopping file watcher...")
        self.handler.flush_sync()
        self._observer.stop()
        self._observer.join(timeout=5)
        self._running = False
        logger.info("File watcher stopped.")

    @property
    def is_running(self) -> bool:
        return self._running and self._observer.is_alive()


class RemotePoller:
    """
    Periodically polls the remote SFTP directory to detect changes
    and downloads them to the local side.

    Keeps a cache of the last-known remote file tree and compares
    each poll cycle to find new, modified, and deleted files.

    Overlap protection: if a poll cycle is still running when the next
    one should start, it is silently skipped to prevent concurrent
    SFTP operations on the same connection.

    Runs in a background daemon thread.
    """

    def __init__(self, engine, interval: int = 30):
        self.engine = engine              # SyncEngine instance
        self.interval = interval          # seconds between polls
        self._last_remote: Dict[str, FileInfo] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._busy = False
        self._busy_lock = threading.Lock()
        self._skip_warned = False
        self._running = False

    def start(self):
        """Seed with current remote state and begin polling."""
        logger.info("Starting remote poller (every %ds)...", self.interval)
        try:
            self._last_remote = self.engine.scan_remote()
            logger.info("Initial remote snapshot: %d files.", len(self._last_remote))
        except Exception as e:
            logger.error("Cannot seed remote poller: %s", e)
            self._last_remote = {}

        self._running = True
        self._stop_event.clear()
        self._skip_warned = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the polling loop immediately."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval + 10)
        logger.info("Remote poller stopped.")

    def _poll_loop(self):
        """
        Main loop using event-based wait.
        - Waits at most `interval` seconds between polls.
        - stop() sets the event → loop exits without waiting full interval.
        - If a cycle is still running, skip this tick (overlap protection).
        """
        while self._running:
            # Wait, but wake immediately on stop()
            if self._stop_event.wait(timeout=self.interval):
                break  # stop() was called
            if not self._running:
                break

            # Acquire the busy lock — if already busy, skip this tick
            acquired = self._busy_lock.acquire(blocking=False)
            if not acquired:
                if not self._skip_warned:
                    logger.warning(
                        "Remote poll still in progress after %ds — "
                        "skipping this cycle. Consider increasing poll_interval.",
                        self.interval,
                    )
                    self._skip_warned = True
                continue  # skip this tick, wait for next interval

            self._skip_warned = False  # reset warning flag
            try:
                self._poll_once()
            except Exception as e:
                logger.error("Remote poll error: %s", e)
            finally:
                self._busy_lock.release()

    def _poll_once(self):
        """Execute a single poll cycle."""
        t0 = time.monotonic()
        current_remote = self.engine.scan_remote()
        task = self.engine.task

        # Detect and handle new/modified files on remote
        for path, info in current_remote.items():
            if path not in self._last_remote:
                # New file appeared on remote
                action = self.engine.resolve_remote_change(path, info)
                if action == "download":
                    self._do_download(path)

            elif info != self._last_remote[path]:
                # Existing file modified on remote
                action = self.engine.resolve_remote_change(path, info)
                if action == "download":
                    self._do_download(path)

        # Detect and handle deletions on remote
        if task.delete_propagate and task.direction != "local-to-remote":
            for path in self._last_remote:
                if path not in current_remote:
                    self.engine.delete_local_file(path)

        # Update cache
        self._last_remote = current_remote

        elapsed = time.monotonic() - t0
        if elapsed > self.interval * 0.8:
            logger.debug(
                "Poll cycle took %.1fs (interval=%ds).",
                elapsed, self.interval,
            )

    def _do_download(self, rel_path: str):
        """Download a file and log the result."""
        try:
            self.engine.download_file(rel_path)
            logger.info("↓ [poll] %s", rel_path)
        except Exception as e:
            logger.error("Poll download failed: %s — %s", rel_path, e)
