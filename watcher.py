"""iSync — File watcher + remote poller."""
import os, time, threading, fnmatch, logging
from typing import Callable, Optional, Dict

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger("isync.watcher")

class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, root: str, exclude: list, callback: Callable, debounce: float = 2.0):
        super().__init__()
        self.root = root; self.exclude = exclude; self.callback = callback
        self.debounce = debounce
        self._pending: Dict[str, str] = {}
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def on_created(self, e): self._q(e, "created")
    def on_modified(self, e): self._q(e, "modified")
    def on_deleted(self, e): self._q(e, "deleted")
    def on_moved(self, e): self._q(e, "deleted"); self._q_dest(e, "created") if not e.is_directory else None

    def _q(self, e, t):
        try: rel = os.path.relpath(e.src_path, self.root)
        except ValueError: return
        if _ex(rel, self.exclude): return
        # For directories, only care about created/deleted (not modified)
        if e.is_directory and t == "modified": return
        with self._lock:
            self._pending[rel] = t
            if self._timer: self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.start()

    def _q_dest(self, e, t):
        try: rel = os.path.relpath(e.dest_path, self.root)
        except ValueError: return
        if _ex(rel, self.exclude): return
        with self._lock:
            self._pending[rel] = t

    def _flush(self):
        with self._lock:
            p = dict(self._pending); self._pending.clear(); self._timer = None
        for rel, t in p.items():
            try: self.callback(rel, t)
            except Exception as ex: logger.error("Callback %s: %s", rel, ex)

class FileWatcher:
    def __init__(self, root: str, exclude: list, on_change: Callable):
        self.handler = DebouncedHandler(root, exclude, on_change)
        self._obs = Observer(); self._running = False
    def start(self):
        if not os.path.isdir(self.handler.root): return
        self._obs.schedule(self.handler, self.handler.root, recursive=True)
        self._obs.start(); self._running = True
        logger.info("Watcher: %s", self.handler.root)
    def stop(self):
        if not self._running: return
        self._obs.stop(); self._obs.join(5); self._running = False
    @property
    def is_running(self): return self._running

class RemotePoller:
    def __init__(self, syncer, interval: int):
        self.syncer = syncer; self.interval = interval
        self._t: Optional[threading.Thread] = None
        self._stop = threading.Event(); self._lock = threading.Lock()
        self._running = False
    def start(self):
        self._running = True; self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True); self._t.start()
        logger.info("Poller: every %ds", self.interval)
    def stop(self):
        self._running = False; self._stop.set()
        if self._t and self._t.is_alive(): self._t.join(self.interval + 10)
    def _loop(self):
        while self._running:
            if self._stop.wait(self.interval): break
            if not self._running: break
            if not self._lock.acquire(blocking=False): continue
            try:
                t0 = time.monotonic()
                self.syncer.poll_remote()
                logger.info("轮询完成 (%.1fs)", time.monotonic() - t0)
            except Exception as e:
                logger.error("Poll error: %s", e)
            finally:
                self._lock.release()
    @property
    def is_running(self): return self._running

def _ex(rel, pats):
    for p in pats:
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p): return True
    return False
