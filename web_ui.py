"""
iSync — Web UI
Flask-based configuration editor and status dashboard.
Run with: python3 main.py web
"""

import os
import json
import logging
from pathlib import Path

from flask import Flask, request, jsonify, render_template_string
import yaml

from config import Config

logger = logging.getLogger("isync.web")

app = Flask(__name__)

# Default config path, can be overridden
CONFIG_PATH = os.path.expanduser("config.yaml")

# ── HTML template ───────────────────────────────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>iSync — Web Console</title>
<style>
:root {
  --bg: #1a1a2e; --panel: #16213e; --accent: #0f3460; --green: #00c853;
  --red: #ff5252; --yellow: #ffd740; --text: #e0e0e0; --muted: #888;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace; background: var(--bg); color: var(--text); min-height: 100vh; }
.header { background: var(--panel); padding: 16px 24px; border-bottom: 2px solid var(--accent); display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; }
.header h1 span { color: var(--green); }
.tabs { display: flex; gap: 4px; padding: 12px 24px 0; }
.tab { padding: 8px 20px; border: none; border-radius: 6px 6px 0 0; cursor: pointer; font-size: 14px; background: var(--panel); color: var(--muted); }
.tab.active { background: var(--accent); color: #fff; }
.panel { background: var(--panel); margin: 0 24px 24px; padding: 24px; border-radius: 0 8px 8px 8px; }
.hidden { display: none; }
textarea { width: 100%; height: 420px; background: #0d1117; color: var(--text); border: 1px solid var(--accent); border-radius: 6px; padding: 14px; font-family: 'SF Mono', monospace; font-size: 13px; resize: vertical; }
.btn { padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; transition: opacity .2s; }
.btn:hover { opacity: .85; }
.btn-save { background: var(--green); color: #000; }
.btn-validate { background: var(--accent); color: #fff; }
.btn-group { display: flex; gap: 10px; margin-top: 14px; }
.toast { padding: 10px 16px; border-radius: 6px; margin-top: 12px; font-size: 13px; }
.toast.success { background: #1b5e20; color: var(--green); }
.toast.error { background: #b71c1c; color: var(--red); }
.status-card { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.stat { background: var(--accent); padding: 16px; border-radius: 8px; text-align: center; }
.stat .num { font-size: 28px; font-weight: 700; }
.stat .label { font-size: 12px; color: var(--muted); margin-top: 4px; }
.task-list { margin-top: 16px; }
.task-item { padding: 12px; border-bottom: 1px solid var(--accent); display: flex; justify-content: space-between; align-items: center; }
.task-name { font-weight: 600; }
.task-detail { font-size: 12px; color: var(--muted); }
.log-view { background: #0d1117; padding: 16px; border-radius: 6px; height: 300px; overflow-y: auto; font-size: 12px; line-height: 1.6; }
</style>
</head>
<body>
<div class="header">
  <h1>iSync <span>Web Console</span></h1>
  <span style="color:var(--muted);font-size:13px" id="config-path"></span>
</div>
<div class="tabs">
  <button class="tab active" onclick="showTab('config')">Config</button>
  <button class="tab" onclick="showTab('tasks')">Tasks</button>
  <button class="tab" onclick="showTab('logs')">Logs</button>
</div>

<!-- Config Tab -->
<div id="tab-config" class="panel">
  <textarea id="yaml-editor" spellcheck="false"></textarea>
  <div class="btn-group">
    <button class="btn btn-validate" onclick="doValidate()">Validate</button>
    <button class="btn btn-save" onclick="doSave()">Save</button>
  </div>
  <div id="toast"></div>
</div>

<!-- Tasks Tab -->
<div id="tab-tasks" class="panel hidden">
  <div id="task-list" class="task-list"></div>
</div>

<!-- Logs Tab -->
<div id="tab-logs" class="panel hidden">
  <div class="log-view" id="log-content">No logs loaded.</div>
</div>

<script>
const CONFIG_PATH = '{{ config_path }}';

function showTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    t.classList.toggle('active', t.innerText.toLowerCase().startsWith(name) || (name==='config'&&i===0) || (name==='tasks'&&i===1) || (name==='logs'&&i===2));
  });
  ['config','tasks','logs'].forEach(id => document.getElementById('tab-'+id).classList.toggle('hidden', id !== name));
  if (name === 'tasks') loadTasks();
  if (name === 'logs') loadLogs();
}

async function loadConfig() {
  const r = await fetch('/api/config');
  const data = await r.json();
  document.getElementById('yaml-editor').value = data.yaml;
  document.getElementById('config-path').textContent = data.path;
}
async function doSave() {
  const yaml = document.getElementById('yaml-editor').value;
  const r = await fetch('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({yaml}) });
  const data = await r.json();
  toast(data.status==='ok' ? 'success' : 'error', data.message);
  if (data.status==='ok') loadConfig();
}
async function doValidate() {
  const yaml = document.getElementById('yaml-editor').value;
  const r = await fetch('/api/validate', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({yaml}) });
  const data = await r.json();
  if (data.valid) toast('success', data.message);
  else toast('error', data.errors.join('\\n'));
}
function toast(type, msg) {
  const el = document.getElementById('toast');
  el.className = 'toast ' + type;
  el.textContent = msg;
  setTimeout(() => el.textContent='', 5000);
}
async function loadTasks() {
  const r = await fetch('/api/config');
  const data = await r.json();
  const list = document.getElementById('task-list');
  if (!data.tasks || !data.tasks.length) { list.innerHTML = '<div style="color:var(--muted)">No tasks configured.</div>'; return; }
  list.innerHTML = data.tasks.map(t => {
    const auth = t.auth_type === 'password' ? 'password' : 'key: '+t.ssh_key_path;
    return `<div class="task-item">
      <div><div class="task-name">${t.name}</div>
      <div class="task-detail">${t.local_path} ↔ ${t.remote_user}@${t.remote_host}:${t.remote_port}${t.remote_path} | ${t.direction} | ${auth}</div></div>
      <div class="task-detail">watch:${t.watch} poll:${t.poll_interval}s delete:${t.delete_propagate}</div>
    </div>`;
  }).join('');
}
async function loadLogs() {
  const r = await fetch('/api/logs');
  const data = await r.json();
  document.getElementById('log-content').innerHTML = data.lines || 'No logs.';
}
loadConfig();
</script>
</body>
</html>"""

# ── routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(PAGE, config_path=CONFIG_PATH)


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global CONFIG_PATH
    if request.method == "GET":
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                yaml_text = f.read()
        except FileNotFoundError:
            yaml_text = "# No config found\nsync_tasks: []\nglobal:\n  log_level: INFO\n"
        cfg = Config(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
        tasks = []
        if cfg and cfg.tasks:
            for t in cfg.tasks:
                tasks.append({
                    "name": t.name, "local_path": t.local_path,
                    "remote_host": t.remote_host, "remote_port": t.remote_port,
                    "remote_user": t.remote_user, "remote_path": t.remote_path,
                    "auth_type": t.auth_type, "ssh_key_path": t.ssh_key_path,
                    "direction": t.direction, "watch": t.watch,
                    "poll_interval": t.poll_interval, "delete_propagate": t.delete_propagate,
                })
        return jsonify({"yaml": yaml_text, "path": CONFIG_PATH, "tasks": tasks})

    # POST: save config
    data = request.get_json()
    yaml_text = data.get("yaml", "")
    try:
        # Validate YAML syntax
        yaml.safe_load(yaml_text)
        # Write to temp first, then rename
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        os.replace(tmp_path, CONFIG_PATH)
        # Re-validate
        cfg = Config(CONFIG_PATH)
        errors = cfg.validate()
        if errors:
            return jsonify({"status": "warn", "message": f"Saved but {len(errors)} validation issue(s): " + "; ".join(errors)})
        return jsonify({"status": "ok", "message": "Configuration saved."})
    except yaml.YAMLError as e:
        return jsonify({"status": "error", "message": f"YAML syntax error: {e}"})


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json()
    yaml_text = data.get("yaml", "")
    try:
        parsed = yaml.safe_load(yaml_text)
        if parsed is None:
            return jsonify({"valid": False, "errors": ["Empty or invalid YAML"]})
        # Write to temp and validate
        import tempfile
        tmp = tempfile.mktemp(suffix=".yaml")
        with open(tmp, "w") as f:
            f.write(yaml_text)
        cfg = Config(tmp)
        errors = cfg.validate()
        os.unlink(tmp)
        if errors:
            return jsonify({"valid": False, "errors": errors})
        return jsonify({"valid": True, "message": f"✅ {len(cfg.tasks)} task(s), all valid."})
    except yaml.YAMLError as e:
        return jsonify({"valid": False, "errors": [f"YAML error: {e}"]})


@app.route("/api/logs")
def api_logs():
    """Show recent sync log records if sync_log_dir is configured."""
    cfg = Config(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
    log_dir = ""
    if cfg:
        log_dir = os.path.expanduser(cfg.global_config.sync_log_dir or "")
    lines = ""
    if log_dir and os.path.isdir(log_dir):
        files = sorted([f for f in os.listdir(log_dir) if f.endswith(".json")], reverse=True)[:50]
        for fname in files:
            fpath = os.path.join(log_dir, fname)
            try:
                with open(fpath) as f:
                    rec = json.load(f)
                s = rec.get("summary", {})
                lines += (f"<div style='margin-bottom:6px'>"
                          f"<b>{rec.get('timestamp','?')[:19]}</b> — {rec.get('task_name','')} "
                          f"↑{s.get('uploaded',0)} ↓{s.get('downloaded',0)} "
                          f"skip:{s.get('skipped',0)} err:{s.get('errors',0)}</div>")
            except Exception:
                lines += f"<div>{fname}</div>"
    elif not log_dir:
        lines = "<span style='color:var(--muted)'>Set sync_log_dir in config to enable sync records.</span>"
    else:
        lines = "<span style='color:var(--muted)'>No sync records yet.</span>"
    return jsonify({"lines": lines})


def run_web(config_path: str = "config.yaml", host: str = "127.0.0.1", port: int = 8080):
    """Start the web UI server."""
    global CONFIG_PATH
    CONFIG_PATH = os.path.expanduser(config_path)
    print(f"\n  iSync Web UI → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
