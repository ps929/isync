#!/usr/bin/env python3
"""
iSync — SSH-based bidirectional file synchronization tool.

Usage:
    python main.py sync [--config config.yaml] [--task my-sync] [--once]
    python main.py list [--config config.yaml]
    python main.py validate [--config config.yaml]
"""

from cli import main

if __name__ == "__main__":
    main()
