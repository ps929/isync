"""
iSync - Command-line interface
Provides sync, list, and validate subcommands.
"""

import os
import sys
import signal
import argparse
import logging
from typing import Optional

from config import Config, SyncTask
from sftp_client import SFTPClient, ConnectionError
from sync_engine import SyncEngine
from watcher import FileWatcher, RemotePoller
from display import SyncDisplay
from logger import setup_logging

logger = logging.getLogger("isync")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isync",
        description="iSync — SSH-based bidirectional file synchronization tool",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── sync ──────────────────────────────────────────────────
    sync_parser = subparsers.add_parser("sync", help="Run file synchronization")
    sync_parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    sync_parser.add_argument(
        "--task", "-t",
        help="Run a specific task by name (default: run all tasks)",
    )
    sync_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync pass, then exit (no file watching)",
    )
    sync_parser.add_argument(
        "--tui",
        action="store_true",
        help="Show live transfer dashboard (requires rich library)",
    )

    # ── list ──────────────────────────────────────────────────
    list_parser = subparsers.add_parser("list", help="List configured sync tasks")
    list_parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )

    # ── validate ──────────────────────────────────────────────
    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    validate_parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )

    # ── web ───────────────────────────────────────────────────
    web_parser = subparsers.add_parser("web", help="Start web configuration UI")
    web_parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    web_parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    web_parser.add_argument(
        "--port", "-p", type=int, default=8080,
        help="Port (default: 8080)",
    )

    return parser


def cmd_list(config: Config):
    """List all configured sync tasks."""
    if not config.tasks:
        print("No sync tasks configured.")
        return

    print(f"Config: {config.config_path}")
    print(f"Tasks: {len(config.tasks)}")
    print("-" * 60)
    for task in config.tasks:
        auth = f"{task.auth_type}" + (f" ({task.ssh_key_path})" if task.auth_type == "key" else "")
        print(f"  [{task.name}]")
        print(f"    Local:   {task.local_path}")
        print(f"    Remote:  {task.remote_user}@{task.remote_host}:{task.remote_port}{task.remote_path}")
        print(f"    Auth:    {auth}")
        print(f"    Dir:     {task.direction}  |  Conflict: {task.conflict_resolution}")
        print(f"    Watch:   {task.watch}  |  Delete: {task.delete_propagate}")
        if task.watch:
            print(f"    Poll:    every {task.poll_interval}s")
        if task.exclude:
            print(f"    Exclude: {', '.join(task.exclude)}")
        print()


def cmd_validate(config: Config):
    """Validate configuration and print errors."""
    errors = config.validate()
    if errors:
        print(f"❌ Configuration has {len(errors)} error(s):")
        for err in errors:
            print(f"  • {err}")
        sys.exit(1)
    else:
        print(f"✅ Configuration is valid ({len(config.tasks)} task(s)).")


def cmd_sync(config: Config, task_name: str = None, watch: bool = True,
             show_tui: bool = False):
    """
    Run sync for one or all tasks.
    If watch is True and the task has watch enabled, keep running
    and monitor for file changes after the initial sync.

    Watch mode runs two complementary mechanisms:
      - FileWatcher (local  → remote): instant upload on local change
      - RemotePoller (remote → local): periodic scan for remote changes
    """
    tasks = config.tasks
    if task_name:
        task = config.get_task(task_name)
        if task is None:
            print(f"❌ Task not found: '{task_name}'")
            print(f"   Available tasks: {', '.join(t.name for t in config.tasks)}")
            sys.exit(1)
        tasks = [task]

    if not tasks:
        print("No sync tasks configured.")
        return

    # Validate before connecting
    errors = config.validate()
    if errors:
        print(f"❌ Configuration has {len(errors)} error(s):")
        for err in errors:
            print(f"  • {err}")
        sys.exit(1)

    monitors: list = []       # FileWatcher + RemotePoller instances
    shutdown_requested = False

    def _on_shutdown(signum, frame):
        nonlocal shutdown_requested
        logger.info("Received signal %s, shutting down gracefully...", signum)
        shutdown_requested = True
        for m in monitors:
            try:
                m.stop()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _on_shutdown)
    signal.signal(signal.SIGTERM, _on_shutdown)

    # TUI display wraps the entire sync + watch lifecycle
    display = SyncDisplay(task_name=tasks[0].name if len(tasks) == 1 else "iSync",
                          enabled=show_tui)

    with display:
        for task in tasks:
            _run_single_task(task, watch, monitors, shutdown_requested,
                             config.global_config.max_clock_skew,
                             config.global_config.sync_log_dir,
                             config.global_config.sync_log_max_files,
                             config.global_config.sync_log_max_days,
                             display=display)

        # If any monitors are running, wait indefinitely
        if monitors:
            logger.info("All monitors running. Press Ctrl+C to stop.")
            try:
                while not shutdown_requested:
                    alive = any(m.is_running for m in monitors)
                    if not alive:
                        shutdown_requested = True
                        break
                    signal.pause()
            except KeyboardInterrupt:
                pass
            finally:
                for m in monitors:
                    m.stop()
                logger.info("All monitors stopped. Goodbye.")


def _run_single_task(task: SyncTask, watch: bool, monitors: list, shutdown: bool,
                     max_clock_skew: int = 300, sync_log_dir: str = "",
                     sync_log_max_files: int = 500, sync_log_max_days: int = 30,
                     display: Optional[SyncDisplay] = None):
    """Run sync for one task. Returns True on success."""
    logger.info("━━━ Task: %s ━━━", task.name)

    try:
        sftp = SFTPClient(
            host=task.remote_host,
            port=task.remote_port,
            user=task.remote_user,
            auth_type=task.auth_type,
            password=task.password,
            ssh_key_path=task.ssh_key_path,
        )
        sftp.connect()
    except ConnectionError as e:
        logger.error("Connection failed for '%s': %s", task.name, e)
        return False

    try:
        engine = SyncEngine(task, sftp, max_clock_skew=max_clock_skew,
                            sync_log_dir=sync_log_dir,
                            sync_log_max_files=sync_log_max_files,
                            sync_log_max_days=sync_log_max_days,
                            display=display)
        stats = engine.sync()

        if stats["errors"] > 0:
            logger.warning("Sync completed with %d error(s).", stats["errors"])

        # Start continuous monitoring if requested
        do_watch = watch and task.watch
        if do_watch and not shutdown:
            local_path = task.local_path

            # 1) Local → Remote: instant upload via file watcher
            if os.path.isdir(local_path):
                def watch_callback(rel_path, event_type):
                    engine.sync_single(rel_path, event_type)
                fw = FileWatcher(task, on_change=watch_callback)
                fw.start()
                monitors.append(fw)
                logger.info("Local watcher active — changes will upload instantly.")
            else:
                logger.warning("Cannot watch — local path not found: %s", local_path)

            # 2) Remote → Local: periodic poll for remote changes
            if task.direction != "local-to-remote":
                rp = RemotePoller(engine, interval=task.poll_interval)
                rp.start()
                monitors.append(rp)
                logger.info("Remote poller active — polling every %ds.", task.poll_interval)
            else:
                logger.info("Remote poller skipped (direction: local-to-remote).")

        return stats["errors"] == 0

    finally:
        # Close SFTP if not monitoring (watch mode keeps it open)
        if not (watch and task.watch):
            sftp.disconnect()


def main():
    """Entry point for the CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Load config (needed for all commands)
    config_path = os.path.expanduser(getattr(args, "config", "config.yaml"))

    if args.command == "validate":
        cfg = Config(config_path)
        # set up minimal logging
        setup_logging(level="WARNING")
        cmd_validate(cfg)
        return

    if args.command == "list":
        cfg = Config(config_path)
        setup_logging(level="WARNING")
        cmd_list(cfg)
        return

    if args.command == "sync":
        cfg = Config(config_path)
        setup_logging(
            level=cfg.global_config.log_level,
            log_file=cfg.global_config.log_file,
        )
        watch = not args.once
        cmd_sync(cfg, task_name=args.task, watch=watch, show_tui=args.tui)
        return

    if args.command == "web":
        from web_ui import run_web
        setup_logging(level="WARNING")
        run_web(config_path=args.config, host=args.host, port=args.port)
        return


if __name__ == "__main__":
    main()
