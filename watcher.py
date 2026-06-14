"""
iSync — File watcher + remote poller
Uses engine's state-based methods for incremental sync.
"""

import os
import time
import threading
import fnmatch
import logging
from typing import Dict, Optional, Callable

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger("isync.watcher")


class DebouncedHandler(FileSystemEventHandler):
    """Watchdog handler with debounce — groups rapid events."""

    def __init__(self, local_root: str, exclude: list,
                 callback: Callable, debounce_seconds: float = 2.0):
        super().__init__()
        self.local_root = local_root
        self.exclude = exclude
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._pending: Dict[str, str] = {}
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def on_created(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path, "modified")

    def on_deleted(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path, "deleted")

    def on_moved(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path, "deleted")
            self._enqueue(event.dest_path, "created")

    def _enqueue(self, abs_path: str, event_type: str):
        try:
            rel_path = os.path.relpath(abs_path, self.local_root)
        except ValueError:
            return
        if self._is_excluded(rel_path):
            return
        with self._lock:
            self._pending[rel_path] = event_type
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._flush)
            self._timer.start()

    def _flush(self):
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None
        for rel_path, event_type in pending.items():
            try:
                self.callback(rel_path, event_type)
            except Exception as e:
                logger.error("Watcher callback error: %s", e)

    def _is_excluded(self, rel_path: str) -> bool:
        for pat in self.exclude:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            if fnmatch.fnmatch(os.path.basename(rel_path), pat):
                return True
        return False

    def flush_sync(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._flush()


class FileWatcher:
    """Watches local dir and triggers sync via engine.sync_local_change()."""

    def __init__(self, local_root: str, exclude: list,
                 on_change: Callable):
        self.handler = DebouncedHandler(local_root, exclude, on_change)
        self._observer = Observer()
        self._running = False

    def start(self):
        if not os.path.isdir(self.handler.local_root):
            logger.warning("Local path not found: %s", self.handler.local_root)
            return
        self._observer.schedule(self.handler, self.handler.local_root,
                                recursive=True)
        self._observer.start()
        self._running = True
        logger.info("Local watcher started: %s", self.handler.local_root)

    def stop(self):
        if not self._running:
            return
        self.handler.flush_sync()
        self._observer.stop()
        self._observer.join(timeout=5)
        self._running = False
        logger.info("Local watcher stopped.")

    @property
    def is_running(self) -> bool:
        return self._running


class RemotePoller:
    """
    Periodically calls engine.poll_remote_changes() which diffs
    current remote state against the stored state table.
    """

    def __init__(self, engine, interval: int = 10):
        self.engine = engine
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._busy_lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Remote poller started (every %ds)", self.interval)

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval + 10)

    def _loop(self):
        while self._running:
            if self._stop_event.wait(timeout=self.interval):
                break
            if not self._running:
                break
            acquired = self._busy_lock.acquire(blocking=False)
            if not acquired:
                logger.debug("Poll skipped — previous still running")
                continue
            try:
                t0 = time.monotonic()
                diff = self.engine.poll_remote_changes()
                elapsed = time.monotonic() - t0
                added = len(diff.get("added", []))
                mod = len(diff.get("modified", []))
                deleted = len(diff.get("deleted", []))
                if added or mod or deleted:
                    logger.info("轮询结果: +%d新 ~%d改 -%d删 (%.1fs)",
                                 added, mod, deleted, elapsed)
                else:
                    logger.info("轮询: 无变化 (%.1fs)", elapsed)
            except Exception as e:
                logger.error("Poll error: %s", e)
            finally:
                self._busy_lock.release()

    @property
    def is_running(self) -> bool:
        return self._running
