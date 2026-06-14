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
<title>iSync Console</title>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --accent: #58a6ff; --green: #3fb950; --red: #f85149;
  --yellow: #d2991d; --text: #c9d1d9; --muted: #8b949e;
  --radius: 8px; --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font); background: var(--bg); color: var(--text); }
.layout { display: flex; height: 100vh; }
.sidebar { width: 260px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.sidebar-header { padding: 20px 16px; border-bottom: 1px solid var(--border); }
.sidebar-header h1 { font-size: 18px; font-weight: 700; }
.sidebar-header h1 .dot { color: var(--green); }
.sidebar-nav { flex: 1; padding: 12px 8px; }
.nav-item { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 12px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; color: var(--muted); background: none; text-align: left; transition: all .15s; }
.nav-item:hover { background: #1c2129; color: var(--text); }
.nav-item.active { background: #1f2937; color: #fff; }
.nav-item .icon { font-size: 18px; width: 22px; text-align: center; }
.task-chip { display: flex; align-items: center; gap: 8px; padding: 8px 12px; margin: 4px 0; border-radius: 6px; font-size: 13px; color: var(--muted); cursor: pointer; transition: all .15s; }
.task-chip:hover { background: #1c2129; color: var(--text); }
.task-chip.active { background: #1f2937; color: #fff; border-left: 3px solid var(--accent); }
.task-chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); flex-shrink: 0; }
.sidebar-footer { padding: 12px 16px; border-top: 1px solid var(--border); font-size: 12px; color: var(--muted); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { padding: 14px 24px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.topbar h2 { font-size: 16px; font-weight: 600; }
.btn { padding: 8px 18px; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: all .15s; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover { opacity: .85; }
.btn-primary { background: var(--green); color: #000; border-color: var(--green); }
.btn-accent { background: var(--accent); color: #000; border-color: var(--accent); }
.btn-danger { background: none; color: var(--red); border-color: var(--red); }
.btn-ghost { background: none; color: var(--text); }
.content { flex: 1; overflow-y: auto; padding: 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 16px; }
.card-title { font-size: 14px; font-weight: 600; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-group { display: flex; flex-direction: column; gap: 4px; }
.form-group.full { grid-column: 1 / -1; }
.form-group label { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
.form-group input, .form-group select, .form-group textarea { padding: 8px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px; font-family: var(--font); }
.form-group input:focus, .form-group select:focus { border-color: var(--accent); outline: none; }
.form-group textarea { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; resize: vertical; min-height: 200px; }
.form-group .hint { font-size: 11px; color: var(--muted); }
.toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 20px; border-radius: 8px; font-size: 13px; z-index: 99; animation: slideIn .3s ease; opacity: 0; transition: opacity .3s; }
.toast.show { opacity: 1; }
.toast.success { background: #1b3a1b; border: 1px solid var(--green); color: var(--green); }
.toast.error { background: #3a1b1b; border: 1px solid var(--red); color: var(--red); }
.toast.warn { background: #3a351b; border: 1px solid var(--yellow); color: var(--yellow); }
@keyframes slideIn { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.empty { color: var(--muted); font-style: italic; padding: 24px; text-align: center; }
.log-line { padding: 4px 0; border-bottom: 1px solid var(--border); font-size: 12px; font-family: 'SF Mono', monospace; }
.log-line .ts { color: var(--muted); margin-right: 8px; }
.log-line .up { color: var(--accent); }
.log-line .down { color: var(--green); }
.log-line .err { color: var(--red); }
.toggle-row { display: flex; align-items: center; gap: 8px; }
.toggle { width: 40px; height: 22px; border-radius: 11px; border: none; cursor: pointer; position: relative; transition: .2s; background: var(--border); }
.toggle.on { background: var(--green); }
.toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: .2s; }
.toggle.on::after { left: 20px; }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-header">
      <h1>iSync<span class="dot">●</span></h1>
      <div style="font-size:12px;color:var(--muted);margin-top:4px">File Sync Console</div>
    </div>
    <nav class="sidebar-nav">
      <button class="nav-item active" onclick="switchView('config')"><span class="icon">⚙</span> Configuration</button>
      <button class="nav-item" onclick="switchView('tasks')"><span class="icon">📋</span> Tasks Overview</button>
      <button class="nav-item" onclick="switchView('logs')"><span class="icon">📜</span> Sync History</button>
      <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
        <div style="font-size:11px;color:var(--muted);padding:0 12px 8px;text-transform:uppercase">TASKS</div>
        <div id="sidebar-tasks"></div>
      </div>
    </nav>
    <div class="sidebar-footer" id="config-path">config.yaml</div>
  </aside>

  <main class="main">
    <!-- CONFIG VIEW -->
    <div id="view-config">
      <div class="topbar">
        <h2>⚙ Configuration</h2>
        <div class="row" style="gap:8px">
          <button class="btn btn-accent" onclick="doValidate()">✓ Validate</button>
          <button class="btn btn-primary" onclick="doSave()">💾 Save</button>
        </div>
      </div>
      <div class="content">
        <div class="card">
          <div class="card-title">📝 YAML Editor</div>
          <div class="form-group"><textarea id="yaml-editor" spellcheck="false" rows="22"></textarea></div>
        </div>
        <div id="global-card" class="card">
          <div class="card-title">🌐 Global Settings</div>
          <div class="form-grid">
            <div class="form-group"><label>Log Level</label><select id="g-log-level"><option>INFO</option><option>DEBUG</option><option>WARNING</option><option>ERROR</option></select></div>
            <div class="form-group"><label>Log File</label><input id="g-log-file" placeholder="empty = console only"></div>
            <div class="form-group"><label>Clock Skew Max (seconds)</label><input id="g-clock-skew" type="number" value="300"></div>
            <div class="form-group"><label>Sync Log Directory</label><input id="g-sync-log-dir" placeholder="empty = disabled"></div>
            <div class="form-group"><label>Max Log Files</label><input id="g-max-files" type="number" value="500"></div>
            <div class="form-group"><label>Max Log Days</label><input id="g-max-days" type="number" value="30"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- TASKS VIEW -->
    <div id="view-tasks" style="display:none">
      <div class="topbar"><h2>📋 Tasks Overview</h2><span style="font-size:13px;color:var(--muted)" id="task-count"></span></div>
      <div class="content" id="tasks-content"></div>
    </div>

    <!-- LOGS VIEW -->
    <div id="view-logs" style="display:none">
      <div class="topbar"><h2>📜 Sync History</h2></div>
      <div class="content" id="logs-content"></div>
    </div>
  </main>
</div>
<div id="toast" class="toast"></div>

<script>
const cfgPath = '{{ config_path }}';
let allTasks = [];

function switchView(v) {
  ['config','tasks','logs'].forEach(id => document.getElementById('view-'+id).style.display = id===v?'':'none');
  document.querySelectorAll('.nav-item').forEach((b,i) => b.classList.toggle('active', (v==='config'&&i===0)||(v==='tasks'&&i===1)||(v==='logs'&&i===2)));
  if (v==='tasks') renderTasks();
  if (v==='logs') loadLogs();
}

// ── Config ──────────────────────────────────────────────
async function init() {
  const r = await fetch('/api/config'); const d = await r.json();
  document.getElementById('yaml-editor').value = d.yaml;
  document.getElementById('config-path').textContent = d.path;
  allTasks = d.tasks || [];
  renderSidebarTasks();
  // Parse global settings
  try {
    const lines = d.yaml.split('\n'); let inGlobal = false;
    for (const l of lines) {
      if (l.trim().startsWith('global:')) { inGlobal = true; continue; }
      if (inGlobal && l.trim().startsWith('sync_tasks:')) break;
      if (inGlobal) {
        if (l.includes('log_level:')) document.getElementById('g-log-level').value = l.split(':')[1].trim().replace(/"/g,'');
        if (l.includes('log_file:')) document.getElementById('g-log-file').value = l.split(':')[1].trim().replace(/"/g,'');
        if (l.includes('max_clock_skew:')) document.getElementById('g-clock-skew').value = parseInt(l.split(':')[1])||300;
        if (l.includes('sync_log_dir:')) document.getElementById('g-sync-log-dir').value = l.split(':')[1].trim().replace(/"/g,'');
        if (l.includes('sync_log_max_files:')) document.getElementById('g-max-files').value = parseInt(l.split(':')[1])||500;
        if (l.includes('sync_log_max_days:')) document.getElementById('g-max-days').value = parseInt(l.split(':')[1])||30;
      }
    }
  } catch(e) {}
}
async function doSave() {
  rebuildYamlFromForm();
  const y = document.getElementById('yaml-editor').value;
  const r = await fetch('/api/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml:y})});
  const d = await r.json();
  showToast(d.status==='ok'?'success':d.status==='warn'?'warn':'error', d.message);
  if (d.status!=='error') init();
}
async function doValidate() {
  rebuildYamlFromForm();
  const y = document.getElementById('yaml-editor').value;
  const r = await fetch('/api/validate', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml:y})});
  const d = await r.json();
  showToast(d.valid?'success':'error', d.valid ? d.message : d.errors.join('\n'));
}
function rebuildYamlFromForm() {
  const gl = document.getElementById('g-log-level').value;
  const gf = document.getElementById('g-log-file').value;
  const gs = document.getElementById('g-clock-skew').value;
  const gd = document.getElementById('g-sync-log-dir').value;
  const gmf = document.getElementById('g-max-files').value;
  const gmd = document.getElementById('g-max-days').value;
  let y = document.getElementById('yaml-editor').value;
  // Replace global section
  const globalBlock = `global:\n  log_level: "${gl}"\n  log_file: "${gf}"\n  max_clock_skew: ${gs}\n  sync_log_dir: "${gd}"\n  sync_log_max_files: ${gmf}\n  sync_log_max_days: ${gmd}`;
  if (y.includes('global:')) {
    y = y.replace(/global:[\s\S]*?(?=\n\S|$)/, globalBlock);
  } else {
    y = y.trim() + '\n\n' + globalBlock + '\n';
  }
  document.getElementById('yaml-editor').value = y;
}
function showToast(type, msg) {
  const t = document.getElementById('toast');
  t.className = 'toast '+type+' show'; t.textContent = msg;
  setTimeout(() => t.classList.remove('show'), 4000);
}

// ── Tasks ───────────────────────────────────────────────
function renderSidebarTasks() {
  document.getElementById('sidebar-tasks').innerHTML = allTasks.map((t,i) =>
    `<div class="task-chip" onclick="switchView('tasks')">
      <span class="dot"></span>${t.name}
    </div>`
  ).join('') || '<div class="empty" style="padding:8px 12px">No tasks</div>';
}
function renderTasks() {
  document.getElementById('task-count').textContent = allTasks.length + ' task(s)';
  if (!allTasks.length) {
    document.getElementById('tasks-content').innerHTML = '<div class="empty">No sync tasks configured. Use the Configuration editor to add tasks.</div>';
    return;
  }
  document.getElementById('tasks-content').innerHTML = allTasks.map(t => `
    <div class="card">
      <div class="card-title">📁 ${t.name}</div>
      <div class="form-grid">
        <div class="form-group full">
          <label>Local Path</label>
          <input value="${t.local_path}" readonly style="background:var(--bg)">
        </div>
        <div class="form-group"><label>Remote Host</label><input value="${t.remote_host}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Port</label><input value="${t.remote_port}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Remote User</label><input value="${t.remote_user}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Remote Path</label><input value="${t.remote_path}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Auth</label><input value="${t.auth_type==='password'?'Password':'SSH Key: '+t.ssh_key_path}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Direction</label><input value="${t.direction}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Conflict</label><input value="${t.conflict_resolution||'newer'}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Watch</label><input value="${t.watch?'✅ On':'❌ Off'}" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Poll Interval</label><input value="${t.poll_interval}s" readonly style="background:var(--bg)"></div>
        <div class="form-group"><label>Delete Propagate</label><input value="${t.delete_propagate?'✅ Yes':'❌ No'}" readonly style="background:var(--bg)"></div>
      </div>
    </div>
  `).join('');
}

// ── Logs ────────────────────────────────────────────────
async function loadLogs() {
  const r = await fetch('/api/logs'); const d = await r.json();
  document.getElementById('logs-content').innerHTML = d.lines ? `<div class="log-view">${d.lines}</div>` : '<div class="empty">No sync records. Set <code>sync_log_dir</code> in config to enable history.</div>';
}

init();
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
                    "direction": t.direction, "conflict_resolution": t.conflict_resolution,
                    "watch": t.watch, "poll_interval": t.poll_interval,
                    "delete_propagate": t.delete_propagate,
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
