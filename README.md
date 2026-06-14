# iSync

SSH-based bidirectional file synchronization tool. Lightweight, single-binary, zero server-side dependencies.

## Features

- **Bidirectional sync** — keep local and remote directories in sync
- **Real-time monitoring** — instant upload on local changes (watchdog) + periodic remote polling
- **Conflict resolution** — newer wins, local always, or remote always
- **Delete propagation** — optionally mirror deletions
- **Exclude patterns** — glob-style filtering (.git, node_modules, *.tmp, etc.)
- **Auto-reconnect** — survives network interruptions
- **TUI dashboard** — live progress bars, transfer speed, file-by-file status
- **Structured logging** — JSON sync records with configurable retention
- **Content hashing** — optional SHA256 head+tail comparison for accurate change detection
- **SFTP** — password or SSH key authentication

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Generate SSH key (if needed)
ssh-keygen -t rsa -f ~/.ssh/id_rsa -N ''

# Edit config
cp config.yaml my-config.yaml
vim my-config.yaml

# Validate
python3 main.py validate

# Run once
python3 main.py sync --task my-sync --once

# Run with live dashboard
python3 main.py sync --task my-sync --tui

# Continuous sync (watch + poll)
python3 main.py sync --task my-sync
```

## Configuration

```yaml
sync_tasks:
  - name: "my-sync"
    local_path: "/home/user/project"
    remote_host: "192.168.1.100"
    remote_port: 22
    remote_user: "user"
    auth_type: "key"              # key or password
    ssh_key_path: "~/.ssh/id_rsa"
    remote_path: "/home/user/backup"
    direction: "bidirectional"    # bidirectional | local-to-remote | remote-to-local
    conflict_resolution: "newer"  # newer | local | remote
    comparison: "mtime"           # mtime | content (SHA256 head+tail)
    watch: true
    poll_interval: 30             # remote poll interval (seconds)
    delete_propagate: true
    exclude:
      - "*.tmp"
      - ".git/**"
      - "node_modules/**"

global:
  log_level: "INFO"
  log_file: ""
  max_clock_skew: 300
  sync_log_dir: ""                # set to enable JSON sync records
  sync_log_max_files: 500
  sync_log_max_days: 30
```

## Requirements

- Python 3.9+
- SSH server on remote side (no additional software needed)

## License

MIT
