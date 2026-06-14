"""iSync config — YAML-based, single task model."""
import os, yaml
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class TaskConfig:
    name: str = "my-sync"
    local_path: str = ""
    remote_host: str = ""
    remote_port: int = 22
    remote_user: str = ""
    auth_type: str = "password"
    password: str = ""
    ssh_key_path: str = "~/.ssh/id_rsa"
    remote_path: str = ""
    direction: str = "bidirectional"
    conflict_resolution: str = "newer"
    poll_interval: int = 10
    delete_propagate: bool = True
    exclude: List[str] = field(default_factory=lambda: [
        "*.tmp", ".git/**", "node_modules/**", "__pycache__/**",
        "*.pyc", ".DS_Store", "Thumbs.db", "~$*", ".isync.db*", ".isync_state*"
    ])
    block_size: int = 131072  # 128KB

@dataclass
class GlobalConfig:
    log_level: str = "INFO"
    sync_log_dir: str = ""
    sync_log_max_files: int = 500
    sync_log_max_days: int = 30

class Config:
    def __init__(self, path="config.yaml"):
        self.path = os.path.expanduser(path)
        self.tasks: List[TaskConfig] = []
        self.global_config = GlobalConfig()
        if os.path.isfile(self.path):
            self._load()

    def _load(self):
        data = yaml.safe_load(open(self.path)) or {}
        g = data.get("global", {})
        self.global_config = GlobalConfig(
            log_level=g.get("log_level", "INFO"),
            sync_log_dir=g.get("sync_log_dir", ""),
            sync_log_max_files=g.get("sync_log_max_files", 500),
            sync_log_max_days=g.get("sync_log_max_days", 30),
        )
        for t in data.get("sync_tasks", []):
            self.tasks.append(TaskConfig(
                name=t.get("name", "my-sync"),
                local_path=os.path.expanduser(t.get("local_path", "")),
                remote_host=t.get("remote_host", ""),
                remote_port=t.get("remote_port", 22),
                remote_user=t.get("remote_user", ""),
                auth_type=t.get("auth_type", "password"),
                password=t.get("password", ""),
                ssh_key_path=os.path.expanduser(t.get("ssh_key_path", "~/.ssh/id_rsa")),
                remote_path=t.get("remote_path", ""),
                direction=t.get("direction", "bidirectional"),
                conflict_resolution=t.get("conflict_resolution", "newer"),
                poll_interval=t.get("poll_interval", 10),
                delete_propagate=t.get("delete_propagate", True),
                **({"exclude": t["exclude"]} if "exclude" in t else {}),
                block_size=t.get("block_size", 131072),
            ))

    def get_task(self, name: str) -> Optional[TaskConfig]:
        for t in self.tasks:
            if t.name == name:
                return t
        return None

    def validate(self) -> List[str]:
        errors = []
        for t in self.tasks:
            if not t.local_path: errors.append(f"{t.name}: local_path required")
            if not t.remote_host: errors.append(f"{t.name}: remote_host required")
            if not t.remote_user: errors.append(f"{t.name}: remote_user required")
            if not t.remote_path: errors.append(f"{t.name}: remote_path required")
            if t.direction not in ("bidirectional", "local-to-remote", "remote-to-local"):
                errors.append(f"{t.name}: invalid direction")
        return errors
