#!/usr/bin/env python3
"""
iSync — SSH-based bidirectional file synchronization.
Default: starts the web console. Use subcommands for CLI mode.

  python3 main.py                 → web console
  python3 main.py sync            → run sync
  python3 main.py sync --tui      → sync with progress dashboard
  python3 main.py validate        → validate config
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Default: web UI
        from web_ui import run_web
        run_web()
    else:
        from cli import main
        main()
