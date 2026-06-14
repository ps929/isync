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
CONFIG_PATH = os.path.expanduser("config.yaml")

# ══════════════════════════════════════════════════════════════════
PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>iSync Console</title>
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d2991d;--text:#c9d1d9;--muted:#8b949e;--radius:8px;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg);color:var(--text)}
.layout{display:flex;height:100vh}
.sidebar{width:240px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.sidebar-header{padding:20px 16px;border-bottom:1px solid var(--border)}
.sidebar-header h1{font-size:18px;font-weight:700}.sidebar-header h1 span{color:var(--green)}
.sidebar-nav{flex:1;padding:12px 8px;overflow-y:auto}
.nav-item{display:flex;align-items:center;gap:8px;width:100%;padding:10px 12px;border:none;border-radius:6px;cursor:pointer;font-size:13px;color:var(--muted);background:none;text-align:left;transition:all .15s}
.nav-item:hover{background:#1c2129;color:var(--text)}.nav-item.active{background:#1f2937;color:#fff}
.sidebar-footer{padding:12px 16px;border-top:1px solid var(--border);font-size:11px;color:var(--muted)}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.topbar{padding:12px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:12px;flex-shrink:0}
.topbar h2{font-size:15px;font-weight:600}
.content{flex:1;overflow-y:auto;padding:20px 24px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:14px}
.card-header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;font-size:13px;font-weight:600}
.card-header .actions{display:flex;gap:6px}
.card-body{padding:16px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.form-grid.col3{grid-template-columns:1fr 1fr 1fr}
.form-grid.col1{grid-template-columns:1fr}
.form-group{display:flex;flex-direction:column;gap:3px}
.form-group.spacer{grid-column:1/-1;height:0;border-top:1px solid var(--border);margin:4px 0}
.form-group label{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.3px}
.form-group input,.form-group select{width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:13px;font-family:var(--font)}
.form-group input:focus,.form-group select:focus{border-color:var(--accent);outline:none}
.form-group input[type=checkbox]{width:auto}
.form-group .hint{font-size:10px;color:var(--muted)}
.form-group textarea{width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:13px;font-family:'SF Mono',monospace;resize:vertical;min-height:60px}
.form-row{display:flex;gap:8px;align-items:center}
.btn{padding:6px 14px;border:1px solid var(--border);border-radius:5px;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s;display:inline-flex;align-items:center;gap:4px;background:var(--surface);color:var(--text)}
.btn:hover{opacity:.85}.btn-sm{padding:4px 10px;font-size:11px}
.btn-primary{background:var(--green);color:#000;border-color:var(--green)}
.btn-accent{background:var(--accent);color:#000;border-color:var(--accent)}
.btn-danger{color:var(--red);border-color:var(--red)}
.toast{position:fixed;bottom:24px;right:24px;padding:12px 20px;border-radius:8px;font-size:13px;z-index:99;opacity:0;transition:opacity .3s;max-width:420px}
.toast.show{opacity:1}.toast.success{background:#1b3a1b;border:1px solid var(--green);color:var(--green)}
.toast.error{background:#3a1b1b;border:1px solid var(--red);color:var(--red)}
.toast.warn{background:#3a351b;border:1px solid var(--yellow);color:var(--yellow)}
@keyframes slideIn{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.empty{color:var(--muted);font-style:italic;padding:24px;text-align:center}
.log-line{padding:3px 0;border-bottom:1px solid var(--border);font-size:11px;font-family:'SF Mono',monospace}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
.tag-on{background:#1b3a1b;color:var(--green)}.tag-off{background:#3a1b1b;color:var(--red)}
</style>
</head>
<body>
<div class="layout">
<aside class="sidebar">
  <div class="sidebar-header"><h1>iSync<span>●</span></h1><div style="font-size:11px;color:var(--muted);margin-top:2px">File Sync Console</div></div>
  <nav class="sidebar-nav">
    <button class="nav-item active" onclick="switchView('config')">⚙ Configuration</button>
    <button class="nav-item" onclick="switchView('logs')">📜 Sync History</button>
  </nav>
  <div class="sidebar-footer" id="config-path">config.yaml</div>
</aside>
<main class="main">
<div id="view-config">
  <div class="topbar">
    <h2>⚙ Configuration</h2>
    <div style="display:flex;gap:8px">
      <button class="btn btn-accent" onclick="doValidate()">✓ Validate</button>
      <button class="btn btn-primary" onclick="doSave()">💾 Save</button>
    </div>
  </div>
  <div class="content" id="config-content"></div>
</div>
<div id="view-logs" style="display:none">
  <div class="topbar"><h2>📜 Sync History</h2></div>
  <div class="content" id="logs-content"><div class="empty">No sync records.</div></div>
</div>
</main>
</div>
<div id="toast" class="toast"></div>

<script>
const cfgPath = '{{ config_path }}';
let tasks = [];

function S(k){return document.getElementById(k)}
function switchView(v){['config','logs'].forEach(id=>S('view-'+id).style.display=id===v?'':'none');document.querySelectorAll('.nav-item').forEach((b,i)=>b.classList.toggle('active',(v==='config'&&i===0)||(v==='logs'&&i===1)));if(v==='logs')loadLogs()}

function showToast(type,msg){const t=S('toast');t.className='toast '+type+' show';t.textContent=msg;setTimeout(()=>t.classList.remove('show'),4000)}

// ── Init ────────────────────────────────────────────────
async function init(){
  const r=await fetch('/api/config');const d=await r.json();
  S('config-path').textContent=d.path;
  tasks=d.tasks||[];
  renderConfig();
  if(d.yaml){try{const p=jsyaml.load(d.yaml);if(p.global)Object.entries(p.global).forEach(([k,v])=>{const el=S('g-'+k);if(el&&typeof v==='string')el.value=v;if(el&&typeof v==='number')el.value=v})}catch(e){}}
}

// ── Render Config ────────────────────────────────────────
function renderConfig(){
  let h='';
  // Global
  h+=`<div class="card"><div class="card-header">🌐 Global Settings</div><div class="card-body"><div class="form-grid col3">
    <div class="form-group"><label>Log Level</label><select id="g-log_level"><option>DEBUG</option><option selected>INFO</option><option>WARNING</option><option>ERROR</option></select></div>
    <div class="form-group"><label>Log File</label><input id="g-log_file" placeholder="empty = console only"></div>
    <div class="form-group"><label>Max Clock Skew (s)</label><input id="g-max_clock_skew" type="number" value="300"><div class="hint">Warn if clocks differ more than this</div></div>
    <div class="form-group"><label>Sync Record Dir</label><input id="g-sync_log_dir" placeholder="empty = no JSON records"></div>
    <div class="form-group"><label>Max Record Files</label><input id="g-sync_log_max_files" type="number" value="500"></div>
    <div class="form-group"><label>Max Record Days</label><input id="g-sync_log_max_days" type="number" value="30"></div>
  </div></div></div>`;

  // Tasks
  tasks.forEach((t,i)=>h+=renderTaskCard(t,i));
  h+=`<div style="text-align:center;padding:12px"><button class="btn" onclick="addTask()">＋ Add Task</button></div>`;
  S('config-content').innerHTML=h;
  // Restore global values
  ['log_level','log_file','max_clock_skew','sync_log_dir','sync_log_max_files','sync_log_max_days'].forEach(k=>{
    const el=S('g-'+k);if(el&&el.tagName==='SELECT')el.value=window['_g_'+k]||'INFO';if(el&&el.tagName==='INPUT')el.value=window['_g_'+k]||'';
  });
}

function renderTaskCard(t,i){
  const authHtml = t.auth_type==='password' ?
    `<div class="form-group"><label>Password</label><input id="t-${i}-password" value="${t.password||''}" placeholder="SSH password"></div>` :
    `<div class="form-group"><label>SSH Key Path</label><input id="t-${i}-ssh_key_path" value="${t.ssh_key_path||'~/.ssh/id_rsa'}"></div>`;
  const direction=t.direction||'bidirectional';
  const conflict=t.conflict_resolution||'newer';
  const comparison=t.comparison||'mtime';
  return `<div class="card">
    <div class="card-header">
      <span>📁 ${t.name||'unnamed'}</span>
      <div class="actions">
        <button class="btn btn-sm btn-danger" onclick="removeTask(${i})">✕ Remove</button>
      </div>
    </div>
    <div class="card-body">
      <div class="form-grid">
        <div class="form-group"><label>Task Name</label><input id="t-${i}-name" value="${t.name||''}" placeholder="my-sync"></div>
        <div class="form-group"><label>Local Path</label><input id="t-${i}-local_path" value="${t.local_path||''}" placeholder="/Users/me/project"></div>
        <div class="form-group"><label>Remote Host</label><input id="t-${i}-remote_host" value="${t.remote_host||''}" placeholder="192.168.1.100"></div>
        <div class="form-group"><label>Remote Port</label><input id="t-${i}-remote_port" type="number" value="${t.remote_port||22}"></div>
        <div class="form-group"><label>Remote User</label><input id="t-${i}-remote_user" value="${t.remote_user||''}" placeholder="root"></div>
        <div class="form-group"><label>Remote Path</label><input id="t-${i}-remote_path" value="${t.remote_path||''}" placeholder="/home/user/sync"></div>
        <div class="form-group"><label>Auth Type</label><select id="t-${i}-auth_type" onchange="rerenderTask(${i})"><option value="key" ${t.auth_type==='key'?'selected':''}>SSH Key</option><option value="password" ${t.auth_type==='password'?'selected':''}>Password</option></select></div>
        ${authHtml}
        <div class="form-group spacer"></div>
        <div class="form-group"><label>Direction</label><select id="t-${i}-direction"><option value="bidirectional" ${direction==='bidirectional'?'selected':''}>Bidirectional</option><option value="local-to-remote" ${direction==='local-to-remote'?'selected':''}>Local → Remote</option><option value="remote-to-local" ${direction==='remote-to-local'?'selected':''}>Remote → Local</option></select></div>
        <div class="form-group"><label>Conflict Resolution</label><select id="t-${i}-conflict_resolution"><option value="newer" ${conflict==='newer'?'selected':''}>Newer Wins</option><option value="local" ${conflict==='local'?'selected':''}>Local Always</option><option value="remote" ${conflict==='remote'?'selected':''}>Remote Always</option></select></div>
        <div class="form-group"><label>Comparison</label><select id="t-${i}-comparison"><option value="mtime" ${comparison==='mtime'?'selected':''}>Mtime + Size (fast)</option><option value="content" ${comparison==='content'?'selected':''}>SHA256 Head+Tail (accurate)</option></select></div>
        <div class="form-group"><label>Poll Interval (s)</label><input id="t-${i}-poll_interval" type="number" value="${t.poll_interval||30}"></div>
        <div class="form-group"><label>Watch</label><select id="t-${i}-watch"><option value="true" ${t.watch!==false?'selected':''}>✅ On</option><option value="false" ${t.watch===false?'selected':''}>❌ Off</option></select></div>
        <div class="form-group"><label>Delete Propagate</label><select id="t-${i}-delete_propagate"><option value="true" ${t.delete_propagate!==false?'selected':''}>✅ Yes</option><option value="false" ${t.delete_propagate===false?'selected':''}>❌ No</option></select></div>
        <div class="form-group spacer"></div>
        <div class="form-group full"><label>Exclude Patterns (one per line)</label><textarea id="t-${i}-exclude" rows="4" placeholder="*.tmp&#10;.git/**&#10;node_modules/**">${(t.exclude||[]).join('\n')}</textarea></div>
      </div>
    </div>
  </div>`;
}

function rerenderTask(i){
  const t=readTaskForm(i);
  tasks[i]=t;
  renderConfig();
}

function addTask(){
  tasks.push({name:'new-task',local_path:'',remote_host:'',remote_port:22,remote_user:'',remote_path:'',auth_type:'key',ssh_key_path:'~/.ssh/id_rsa',password:'',direction:'bidirectional',conflict_resolution:'newer',comparison:'mtime',poll_interval:30,watch:true,delete_propagate:true,exclude:['*.tmp','.git/**','node_modules/**']});
  renderConfig();
}

function removeTask(i){tasks.splice(i,1);renderConfig()}

function readTaskForm(i){
  const p='t-'+i+'-';
  const excludeRaw=S(p+'exclude')?.value||'';
  return {
    name:S(p+'name')?.value||'',local_path:S(p+'local_path')?.value||'',remote_host:S(p+'remote_host')?.value||'',
    remote_port:parseInt(S(p+'remote_port')?.value)||22,remote_user:S(p+'remote_user')?.value||'',
    remote_path:S(p+'remote_path')?.value||'',auth_type:S(p+'auth_type')?.value||'key',
    ssh_key_path:S(p+'ssh_key_path')?.value||'',password:S(p+'password')?.value||'',
    direction:S(p+'direction')?.value||'bidirectional',conflict_resolution:S(p+'conflict_resolution')?.value||'newer',
    comparison:S(p+'comparison')?.value||'mtime',poll_interval:parseInt(S(p+'poll_interval')?.value)||30,
    watch:S(p+'watch')?.value==='true',delete_propagate:S(p+'delete_propagate')?.value==='true',
    exclude:excludeRaw.split('\n').map(s=>s.trim()).filter(s=>s)
  };
}

function readAllTasks(){
  const result=[];
  for(let i=0;i<tasks.length;i++)result.push(readTaskForm(i));
  return result;
}

function buildYaml(){
  const gl={log_level:S('g-log_level')?.value||'INFO',log_file:S('g-log_file')?.value||'',max_clock_skew:parseInt(S('g-max_clock_skew')?.value)||300,sync_log_dir:S('g-sync_log_dir')?.value||'',sync_log_max_files:parseInt(S('g-sync_log_max_files')?.value)||500,sync_log_max_days:parseInt(S('g-sync_log_max_days')?.value)||30};
  const ts=readAllTasks();
  const obj={global:gl,sync_tasks:ts.map(t=>({name:t.name,local_path:t.local_path,remote_host:t.remote_host,remote_port:t.remote_port,remote_user:t.remote_user,auth_type:t.auth_type,password:t.password||'',ssh_key_path:t.ssh_key_path,direction:t.direction,conflict_resolution:t.conflict_resolution,comparison:t.comparison,watch:t.watch,delete_propagate:t.delete_propagate,poll_interval:t.poll_interval,exclude:t.exclude}))};
  return jsyaml.dump(obj,{indent:2,lineWidth:-1,noCompatMode:true});
}

async function doSave(){
  try{
    const y=buildYaml();
    const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml:y})});
    const d=await r.json();
    showToast(d.status==='ok'?'success':d.status==='warn'?'warn':'error',d.message);
  }catch(e){showToast('error','YAML build error: '+e.message)}
}
async function doValidate(){
  try{
    const y=buildYaml();
    const r=await fetch('/api/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml:y})});
    const d=await r.json();
    showToast(d.valid?'success':'error',d.valid?d.message:d.errors.join('\n'));
  }catch(e){showToast('error','YAML build error: '+e.message)}
}
async function loadLogs(){
  const r=await fetch('/api/logs');const d=await r.json();
  S('logs-content').innerHTML=d.lines?`<div style="padding:12px">${d.lines}</div>`:'<div class="empty">No sync records. Set <code>sync_log_dir</code> in config.</div>';
}
init();
</script>
<script src="https://cdn.jsdelivr.net/npm/js-yaml@4.1.0/dist/js-yaml.min.js"></script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════

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
            yaml_text = "sync_tasks: []\nglobal:\n  log_level: INFO\n"
        cfg = Config(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
        tasks = []
        if cfg and cfg.tasks:
            for t in cfg.tasks:
                tasks.append({
                    "name": t.name, "local_path": t.local_path,
                    "remote_host": t.remote_host, "remote_port": t.remote_port,
                    "remote_user": t.remote_user, "remote_path": t.remote_path,
                    "auth_type": t.auth_type, "ssh_key_path": t.ssh_key_path,
                    "password": t.password,
                    "direction": t.direction, "conflict_resolution": t.conflict_resolution,
                    "comparison": t.comparison,
                    "watch": t.watch, "poll_interval": t.poll_interval,
                    "delete_propagate": t.delete_propagate, "exclude": t.exclude,
                })
        return jsonify({"yaml": yaml_text, "path": CONFIG_PATH, "tasks": tasks})

    data = request.get_json()
    yaml_text = data.get("yaml", "")
    try:
        yaml.safe_load(yaml_text)
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        os.replace(tmp_path, CONFIG_PATH)
        cfg = Config(CONFIG_PATH)
        errors = cfg.validate()
        if errors:
            return jsonify({"status": "warn", "message": f"Saved with {len(errors)} warning(s): " + "; ".join(errors)})
        return jsonify({"status": "ok", "message": "✓ Configuration saved successfully."})
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
        import tempfile
        tmp = tempfile.mktemp(suffix=".yaml")
        with open(tmp, "w") as f:
            f.write(yaml_text)
        cfg = Config(tmp)
        errors = cfg.validate()
        os.unlink(tmp)
        if errors:
            return jsonify({"valid": False, "errors": errors})
        return jsonify({"valid": True, "message": f"✓ {len(cfg.tasks)} task(s), configuration valid."})
    except yaml.YAMLError as e:
        return jsonify({"valid": False, "errors": [f"YAML parse error: {e}"]})


@app.route("/api/logs")
def api_logs():
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
                lines += (f"<div class='log-line'><span class='ts'>{rec.get('timestamp','?')[:19]}</span>"
                          f"<b>{rec.get('task_name','')}</b> "
                          f"<span class='up'>↑{s.get('uploaded',0)}</span> "
                          f"<span class='down'>↓{s.get('downloaded',0)}</span> "
                          f"skip:{s.get('skipped',0)} err:{s.get('errors',0)}</div>")
            except Exception:
                lines += f"<div class='log-line'>{fname}</div>"
    if not lines:
        lines = "<div class='empty'>No sync records yet.</div>"
    return jsonify({"lines": lines})


def run_web(config_path: str = "config.yaml", host: str = "127.0.0.1", port: int = 8080):
    global CONFIG_PATH
    CONFIG_PATH = os.path.expanduser(config_path)
    print(f"\n  iSync Web UI → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
