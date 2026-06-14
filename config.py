"""
iSync - Configuration management
Handles loading, validating, and managing sync task configurations.
"""

import os
import yaml
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class SyncTask:
    """A single sync pair configuration."""
    name: str
    local_path: str
    remote_host: str
    remote_port: int = 22
    remote_user: str = ""
    auth_type: str = "key"       # "key" or "password"
    password: str = ""
    ssh_key_path: str = "~/.ssh/id_rsa"
    remote_path: str = ""
    direction: str = "bidirectional"  # "bidirectional", "local-to-remote", "remote-to-local"
    conflict_resolution: str = "newer"  # "newer", "local", "remote"
    watch: bool = True
    delete_propagate: bool = True
    poll_interval: int = 30  # seconds between remote polls in watch mode
    comparison: str = "mtime"  # "mtime" (fast) or "content" (SHA256 head+tail)
    exclude: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Expand paths
        self.local_path = os.path.expanduser(self.local_path)
        self.remote_path = os.path.expanduser(self.remote_path)
        self.ssh_key_path = os.path.expanduser(self.ssh_key_path)
        if self.password:
            self.auth_type = "password"


@dataclass
class GlobalConfig:
    """Global iSync settings."""
    log_level: str = "INFO"
    log_file: str = ""
    max_clock_skew: int = 300  # max acceptable clock diff in seconds (default 5 min)
    sync_log_dir: str = ""  # dir for structured JSON sync records (empty = disabled)
    sync_log_max_files: int = 500  # max records per task before rotation
    sync_log_max_days: int = 30    # max age of records before deletion


class Config:
    """Load and manage iSync configuration from YAML."""

    def __init__(self, config_path: str):
        self.config_path = os.path.expanduser(config_path)
        self.tasks: List[SyncTask] = []
        self.global_config: GlobalConfig = GlobalConfig()
        if os.path.exists(self.config_path):
            self._load()

    def _load(self):
        """Load configuration from YAML file."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            raise ValueError(f"Empty config file: {self.config_path}")

        # Parse global settings
        global_data = data.get("global", {})
        self.global_config = GlobalConfig(
            log_level=global_data.get("log_level", "INFO"),
            log_file=global_data.get("log_file", ""),
            max_clock_skew=global_data.get("max_clock_skew", 300),
            sync_log_dir=global_data.get("sync_log_dir", ""),
            sync_log_max_files=global_data.get("sync_log_max_files", 500),
            sync_log_max_days=global_data.get("sync_log_max_days", 30),
        )

        # Parse sync tasks
        for task_data in data.get("sync_tasks", []):
            task = SyncTask(
                name=task_data.get("name", "unnamed"),
                local_path=task_data.get("local_path", ""),
                remote_host=task_data.get("remote_host", ""),
                remote_port=task_data.get("remote_port", 22),
                remote_user=task_data.get("remote_user", ""),
                auth_type=task_data.get("auth_type", "key"),
                password=task_data.get("password", ""),
                ssh_key_path=task_data.get("ssh_key_path", "~/.ssh/id_rsa"),
                remote_path=task_data.get("remote_path", ""),
                direction=task_data.get("direction", "bidirectional"),
                conflict_resolution=task_data.get("conflict_resolution", "newer"),
                watch=task_data.get("watch", True),
                delete_propagate=task_data.get("delete_propagate", True),
                poll_interval=task_data.get("poll_interval", 30),
                comparison=task_data.get("comparison", "mtime"),
                exclude=task_data.get("exclude", []),
            )
            self.tasks.append(task)

    def get_task(self, name: str) -> Optional[SyncTask]:
        """Get a sync task by name."""
        for task in self.tasks:
            if task.name == name:
                return task
        return None

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        for task in self.tasks:
            if not task.name:
                errors.append("Task has no name")
            if not task.local_path:
                errors.append(f"Task '{task.name}': local_path is required")
            if not task.remote_host:
                errors.append(f"Task '{task.name}': remote_host is required")
            if not task.remote_user:
                errors.append(f"Task '{task.name}': remote_user is required")
            if not task.remote_path:
                errors.append(f"Task '{task.name}': remote_path is required")
            if task.direction not in ("bidirectional", "local-to-remote", "remote-to-local"):
                errors.append(f"Task '{task.name}': invalid direction '{task.direction}'")
            if task.conflict_resolution not in ("newer", "local", "remote"):
                errors.append(f"Task '{task.name}': invalid conflict_resolution '{task.conflict_resolution}'")
            if task.auth_type not in ("key", "password"):
                errors.append(f"Task '{task.name}': auth_type must be 'key' or 'password'")
            if task.auth_type == "key" and not os.path.exists(task.ssh_key_path):
                errors.append(f"Task '{task.name}': SSH key not found at '{task.ssh_key_path}'")
        return errors
