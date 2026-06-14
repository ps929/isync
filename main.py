#!/usr/bin/env python3
"""iSync — SSH/SFTP file sync with block-level indexing. Default: web UI."""
import sys
if len(sys.argv) == 1:
    from web_ui import run_web; run_web()
else:
    print("Usage: python3 main.py")
    print("  Starts the web console at http://127.0.0.1:8080")
