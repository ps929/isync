"""
iSync — Live transfer display (TUI)
Uses rich.live.Live to render a real-time dashboard.
"""

import os
import time
import threading
from typing import List, Optional

from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.console import Group
from rich.text import Text


class SyncDisplay:
    """
    Live display for sync operations. Thread-safe.

    Usage:
        with SyncDisplay("my-task") as d:
            d.on_upload_start("a.txt", 1024)
            d.on_upload_progress("a.txt", 512, 1024)
            d.on_upload_done("a.txt")
    """

    def __init__(self, task_name: str = "", enabled: bool = True):
        self.task_name = task_name
        self.enabled = enabled
        self._lock = threading.Lock()

        # Stats
        self.uploaded = 0
        self.downloaded = 0
        self.deleted_local = 0
        self.deleted_remote = 0
        self.skipped = 0
        self.errors = 0
        self.total_files = 0
        self.done_files = 0

        # Current transfer
        self._current_op: str = ""
        self._current_file: str = ""
        self._current_done: int = 0
        self._current_total: int = 0
        self._current_speed: float = 0.0
        self._last_bytes: int = 0
        self._last_time: float = 0.0

        # Activity log (last N entries)
        self._recent: List[str] = []

        self._live: Optional[Live] = None

    def __enter__(self):
        if self.enabled:
            self._live = Live(
                self._render(),
                refresh_per_second=4,
                transient=False,
                vertical_overflow="visible",
            )
            self._live.__enter__()
        return self

    def __exit__(self, *args):
        if self._live:
            # Final render
            self._live.update(self._render(final=True))
            time.sleep(0.3)
            self._live.__exit__(*args)
        return False

    # ── public callbacks ──────────────────────────────────────────

    def set_total_files(self, n: int):
        with self._lock:
            self.total_files = n

    def on_upload_start(self, path: str, size: int):
        with self._lock:
            self._current_op = "↑"
            self._current_file = path
            self._current_total = size
            self._current_done = 0
            self._last_time = time.monotonic()
            self._last_bytes = 0

    def on_upload_progress(self, path: str, done: int, total: int):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed > 0.5:
                self._current_speed = (done - self._last_bytes) / max(elapsed, 0.001)
                self._last_bytes = done
                self._last_time = now
            self._current_done = done
            self._current_total = total

    def on_upload_done(self, path: str):
        with self._lock:
            self.uploaded += 1
            self.done_files += 1
            self._recent.append(f"[cyan]↑[/cyan] {path}")
            self._trim_recent()
            self._current_op = ""

    def on_download_start(self, path: str, size: int):
        with self._lock:
            self._current_op = "↓"
            self._current_file = path
            self._current_total = size
            self._current_done = 0
            self._last_time = time.monotonic()
            self._last_bytes = 0

    def on_download_progress(self, path: str, done: int, total: int):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed > 0.5:
                self._current_speed = (done - self._last_bytes) / max(elapsed, 0.001)
                self._last_bytes = done
                self._last_time = now
            self._current_done = done
            self._current_total = total

    def on_download_done(self, path: str):
        with self._lock:
            self.downloaded += 1
            self.done_files += 1
            self._recent.append(f"[green]↓[/green] {path}")
            self._trim_recent()
            self._current_op = ""

    def on_delete(self, path: str, side: str):
        with self._lock:
            if side == "local":
                self.deleted_local += 1
                tag = "✗L"
            else:
                self.deleted_remote += 1
                tag = "✗R"
            self.done_files += 1
            self._recent.append(f"[red]{tag}[/red] {path}")
            self._trim_recent()

    def on_error(self, path: str, error: str):
        with self._lock:
            self.errors += 1
            self._recent.append(f"[red]✖[/red] {path}")

    def on_skip(self, count: int):
        with self._lock:
            self.skipped = count

    # ── rendering ─────────────────────────────────────────────────

    def _trim_recent(self, max_items: int = 15):
        if len(self._recent) > max_items:
            self._recent = self._recent[-max_items:]

    def _render(self, final: bool = False):
        # Header
        title = Text(f"iSync — {self.task_name}", style="bold white on blue")

        # Progress bar (text-based)
        if self.total_files > 0:
            pct = min(self.done_files * 100 // self.total_files, 100)
            bar_width = 30
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            progress_line = Text(
                f"[{bar}] {pct}%  ({self.done_files}/{self.total_files})",
                style="bold",
            )
        else:
            progress_line = Text("Waiting for sync plan...", style="dim")

        # Current transfer
        current_line = Text("")
        if self._current_op:
            op_symbol = "↑" if self._current_op == "↑" else "↓"
            fname = os.path.basename(self._current_file)
            if self._current_total > 0:
                pct = self._current_done * 100 // self._current_total
                size_mb = self._current_total / 1024 / 1024
                speed_mb = self._current_speed / 1024 / 1024
                current_line = Text(
                    f"  {op_symbol} {fname}  "
                    f"[{self._mini_bar(pct)}] {pct}%  "
                    f"{size_mb:.1f} MB  {speed_mb:.1f} MB/s",
                    style="bold cyan" if op_symbol == "↑" else "bold green",
                )
            else:
                current_line = Text(f"  {op_symbol} {fname}", style="bold")

        # Stats
        stats_text = Text(
            f"↑{self.uploaded} ↓{self.downloaded} "
            f"✗L:{self.deleted_local} ✗R:{self.deleted_remote} "
            f"skip:{self.skipped} err:{self.errors}",
            style="bold",
        )

        # Recent activity
        recent_text = Text()
        for line in self._recent[-8:]:
            recent_text.append(line + "\n")
        if not recent_text.plain:
            recent_text = Text("Waiting...", style="dim")

        # Compose
        body = Group(
            title,
            Text(""),
            progress_line,
            Text(""),
            current_line,
            Text(""),
            Text("─" * 50, style="dim"),
            stats_text,
            Text(""),
            Panel(recent_text, title="Recent", border_style="green"),
        )
        return body

    @staticmethod
    def _mini_bar(pct: int, width: int = 20) -> str:
        filled = int(width * pct / 100)
        return "█" * filled + "░" * (width - filled)
