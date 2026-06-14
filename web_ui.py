"""
iSync — Web Console (config + sync control)
Run with: python3 main.py
"""

import os, json, logging, yaml, threading, time, queue
from flask import Flask, request, jsonify, render_template_string, Response

from config import Config
from sftp_client import SFTPClient, ConnectionError
from sync_engine import SyncEngine
from watcher import FileWatcher, RemotePoller

logger = logging.getLogger("isync.web")
app = Flask(__name__)
CONFIG_PATH = os.path.expanduser("config.yaml")

# Sync runtime state
_sync_thread = None
_sync_stop = threading.Event()
_log_queue = queue.Queue()
_sync_status = {"running": False, "task": "", "stats": {}}


class _WebLogHandler(logging.Handler):
    """Route Python logging to the web UI log queue."""
    def emit(self, record):
        msg = self.format(record)
        style = ""
        if record.levelno >= logging.ERROR:
            style = "err"
        elif record.levelno >= logging.WARNING:
            style = "dim"
        elif "↑" in msg or "upload" in msg.lower():
            style = "up"
        elif "↓" in msg or "download" in msg.lower():
            style = "down"
        _log_queue.put((msg, style))

PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>iSync</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--text:#c9d1d9;--muted:#8b949e}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);display:flex;justify-content:center;min-height:100vh}
.container{width:100%;max-width:560px;padding:20px}
.tabs{display:flex;gap:4px;margin-bottom:0}
.tab{padding:10px 20px;border:none;border-radius:8px 8px 0 0;cursor:pointer;font-size:14px;background:var(--card);color:var(--muted)}
.tab.active{background:var(--accent);color:#fff}
.card{background:var(--card);border:1px solid var(--border);border-radius:0 12px 12px 12px;padding:24px}
.card h2{font-size:20px;margin-bottom:2px}.card h2 span{color:var(--green)}
.card .sub{font-size:13px;color:var(--muted);margin-bottom:20px}
.row{display:flex;gap:10px;margin-bottom:12px}
.row.col2>div{flex:1}
label{display:block;font-size:11px;font-weight:600;color:var(--muted);margin-bottom:3px;text-transform:uppercase;letter-spacing:.3px}
input,select{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px}
input:focus,select:focus{border-color:var(--accent);outline:none}
.btn{padding:12px 24px;border:none;border-radius:8px;cursor:pointer;font-size:15px;font-weight:600;transition:all .15s}
.btn-save{width:100%;background:var(--green);color:#000;margin-top:8px}.btn-save:hover{opacity:.9}
.btn-start{background:var(--green);color:#000}.btn-stop{background:var(--red);color:#fff}
.btn-group{display:flex;gap:8px;margin-top:12px}
.msg{padding:12px;border-radius:8px;margin-top:12px;font-size:13px;text-align:center}
.msg.ok{background:#1b3a1b;color:var(--green)}.msg.err{background:#3a1b1b;color:var(--red)}.msg.info{background:#1a2a3a;color:var(--accent)}
.console{background:#000;color:var(--green);padding:14px;border-radius:8px;height:240px;overflow-y:auto;font-family:'SF Mono',monospace;font-size:12px;line-height:1.5;margin-top:12px;white-space:pre-wrap}
.console .dim{color:var(--muted)}.console .err{color:var(--red)}.console .up{color:var(--accent)}.console .down{color:var(--green)}
.advanced-toggle{font-size:11px;color:var(--muted);cursor:pointer;margin-top:14px;text-align:center}
.advanced{display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--border)}
.advanced.show{display:block}
.stats-row{display:flex;gap:10px;margin:10px 0;flex-wrap:wrap}
.stat{flex:1;min-width:50px;background:var(--bg);padding:8px;border-radius:6px;text-align:center}
.stat .n{font-size:18px;font-weight:700}.stat .l{font-size:10px;color:var(--muted)}
.footer{text-align:center;margin-top:12px;font-size:11px;color:var(--muted)}
</style>
</head>
<body>
<div class="container">
<div class="tabs">
  <button class="tab active" onclick="showTab('config')">⚙ 配置</button>
  <button class="tab" onclick="showTab('sync')">🔄 同步</button>
</div>

<!-- Config -->
<div id="tab-config" class="card">
  <h2>iSync<span>●</span></h2>
  <div class="sub">所有参数均可配置</div>
  <div class="row col2">
    <div><label>远端 IP</label><input id="host" placeholder="192.168.1.100"></div>
    <div><label>端口</label><input id="port" type="number" value="22"></div>
  </div>
  <div class="row col2">
    <div><label>登录用户名</label><input id="user" placeholder="root"></div>
    <div><label>登录密码</label><input id="password" placeholder="SSH 密码"></div>
  </div>
  <div class="row col2">
    <div><label>本地同步路径</label><input id="local_path" placeholder="/Users/me/sync"></div>
    <div><label>远端路径</label><input id="remote_path" placeholder="E:/sync"></div>
  </div>
  <div class="row col2">
    <div><label>同步方向</label><select id="direction"><option value="bidirectional">双向同步</option><option value="local-to-remote">本地→远端</option><option value="remote-to-local">远端→本地</option></select></div>
    <div><label>冲突处理</label><select id="conflict"><option value="newer">谁新谁赢</option><option value="local">本地优先</option><option value="remote">远端优先</option></select></div>
  </div>
  <div class="row col2">
    <div><label>比对方式</label><select id="comparison"><option value="mtime">时间+大小 (快)</option><option value="content">内容哈希 (准)</option></select></div>
    <div><label>轮询间隔 (秒)</label><input id="poll" type="number" value="1"></div>
  </div>
  <div class="row col2">
    <div><label>实时监控</label><select id="watch"><option value="yes">✅ 开启</option><option value="no">❌ 关闭</option></select></div>
    <div><label>删除传播</label><select id="delete_prop"><option value="yes">✅ 开启</option><option value="no">❌ 关闭</option></select></div>
  </div>
  <label>排除规则 (每行一项)</label>
  <textarea id="exclude" rows="4" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;font-family:monospace">*.tmp
.git/**
node_modules/**
__pycache__/**
*.pyc
.DS_Store</textarea>
  <button class="btn btn-save" onclick="save()">💾 保存配置</button>
  <div id="msg"></div>
  <div class="footer">保存后切换到「同步」Tab 启动</div>
</div>

<!-- Sync -->
<div id="tab-sync" class="card" style="display:none">
  <h2>🔄 同步</h2>
  <div class="sub" id="sync-task-name">my-sync</div>
  <div class="row" style="align-items:center;margin-bottom:12px">
    <span style="font-size:13px;color:var(--muted)">同步模式：</span>
    <select id="sync-mode" style="width:auto">
      <option value="watch" selected>🔄 持续监控（文件变化自动同步）</option>
      <option value="once">📋 单次同步（同步完即停止）</option>
    </select>
  </div>
  <div id="stats-bar" class="stats-row"></div>
  <div class="btn-group">
    <button class="btn btn-start" id="btn-start" onclick="startSync()">▶ 开始同步</button>
    <button class="btn btn-stop" id="btn-stop" onclick="stopSync()" style="display:none">■ 停止</button>
  </div>
  <div class="console" id="console">准备就绪。</div>
</div>
</div>

<script>
let logTimer=null;

function showTab(t){
  document.getElementById('tab-config').style.display=t==='config'?'':'none';
  document.getElementById('tab-sync').style.display=t==='sync'?'':'none';
  document.querySelectorAll('.tab').forEach((b,i)=>b.classList.toggle('active',(t==='config'&&i===0)||(t==='sync'&&i===1)));
  if(t==='sync'){checkStatus();logTimer=setInterval(fetchLogs,1000)}else{clearInterval(logTimer)}
}

async function checkStatus(){
  const r=await fetch('/api/sync/status');const d=await r.json();
  document.getElementById('btn-start').style.display=d.running?'none':'';
  document.getElementById('btn-stop').style.display=d.running?'':'none';
  if(d.stats)renderStats(d.stats);
}
async function startSync(){
  const mode=document.getElementById('sync-mode').value;
  const r=await fetch('/api/sync/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});const d=await r.json();
  if(!d.ok){document.getElementById('console').innerHTML+='<div class="err">'+d.error+'</div>';return}
  document.getElementById('btn-start').style.display='none';
  document.getElementById('btn-stop').style.display='';
  document.getElementById('console').innerHTML='';
  logTimer=setInterval(fetchLogs,1000);
}
async function stopSync(){
  await fetch('/api/sync/stop',{method:'POST'});
  document.getElementById('btn-start').style.display='';
  document.getElementById('btn-stop').style.display='none';
  clearInterval(logTimer);
}
async function fetchLogs(){
  const r=await fetch('/api/sync/logs');const d=await r.json();
  if(d.lines)document.getElementById('console').innerHTML=d.lines;
  if(d.stats)renderStats(d.stats);
  if(!d.running){document.getElementById('btn-start').style.display='';document.getElementById('btn-stop').style.display='none';clearInterval(logTimer)}
}
function renderStats(s){
  document.getElementById('stats-bar').innerHTML=
    `<div class="stat"><div class="n">${s.uploaded||0}</div><div class="l">↑ 上传</div></div>
     <div class="stat"><div class="n">${s.downloaded||0}</div><div class="l">↓ 下载</div></div>
     <div class="stat"><div class="n">${s.skipped||0}</div><div class="l">跳过</div></div>
     <div class="stat"><div class="n" style="color:var(--red)">${s.errors||0}</div><div class="l">错误</div></div>`;
}

async function init(){
  const r=await fetch('/api/config');const d=await r.json();
  if(d.tasks&&d.tasks.length){
    const t=d.tasks[0];
    document.getElementById('host').value=t.remote_host||'';
    document.getElementById('port').value=t.remote_port||22;
    document.getElementById('local_path').value=t.local_path||'';
    document.getElementById('user').value=t.remote_user||'';
    document.getElementById('password').value=t.password||'';
    document.getElementById('remote_path').value=t.remote_path||'';
    document.getElementById('direction').value=t.direction||'bidirectional';
    document.getElementById('conflict').value=t.conflict_resolution||'newer';
    document.getElementById('poll').value=t.poll_interval||30;
    document.getElementById('delete_prop').value=t.delete_propagate!==false?'yes':'no';
    document.getElementById('watch').value=t.watch!==false?'yes':'no';
    document.getElementById('comparison').value=t.comparison||'mtime';
    if(t.exclude&&t.exclude.length)document.getElementById('exclude').value=t.exclude.join('\n');
    document.getElementById('sync-task-name').textContent=t.name||'my-sync';
  }
}

async function save(){
  const msg=document.getElementById('msg');
  try{
    const task={
      name:'my-sync',local_path:document.getElementById('local_path').value.trim(),
      remote_host:document.getElementById('host').value.trim(),
      remote_port:parseInt(document.getElementById('port').value)||22,
      remote_user:document.getElementById('user').value.trim(),
      auth_type:'password',password:document.getElementById('password').value,
      remote_path:document.getElementById('remote_path').value.trim(),
      direction:document.getElementById('direction').value,
      conflict_resolution:document.getElementById('conflict').value,
      comparison:document.getElementById('comparison').value,
      poll_interval:parseInt(document.getElementById('poll').value)||30,
      watch:document.getElementById('watch').value==='yes',
      delete_propagate:document.getElementById('delete_prop').value==='yes',
      exclude:document.getElementById('exclude').value.split('\n').map(s=>s.trim()).filter(s=>s)
    };
    const yaml=[
      'sync_tasks:','  - name: "'+task.name+'"','    local_path: "'+task.local_path+'"',
      '    remote_host: "'+task.remote_host+'"','    remote_port: '+task.remote_port,
      '    remote_user: "'+task.remote_user+'"','    auth_type: password',
      '    password: "'+task.password+'"','    remote_path: "'+task.remote_path+'"',
      '    direction: '+task.direction,'    conflict_resolution: '+task.conflict_resolution,
      '    comparison: '+task.comparison,'    watch: '+(task.watch?'true':'false'),
      '    poll_interval: '+task.poll_interval,'    delete_propagate: '+(task.delete_propagate?'true':'false'),
      '    exclude:',...task.exclude.map(e=>'      - "'+e+'"'),
      '','global:','  log_level: INFO','  log_file: ""','  max_clock_skew: 300',
      '  sync_log_dir: ""','  sync_log_max_files: 500','  sync_log_max_days: 30',
    ].join('\n');
    const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml})});
    const d=await r.json();
    msg.className='msg '+(d.status==='ok'?'ok':d.status==='warn'?'info':'err');
    msg.textContent=d.message;
  }catch(e){msg.className='msg err';msg.textContent=e.message}
}
init();
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════
# Web Routes
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
                    "auth_type": t.auth_type, "password": t.password,
                    "direction": t.direction, "conflict_resolution": t.conflict_resolution,
                    "comparison": t.comparison, "watch": t.watch,
                    "poll_interval": t.poll_interval, "delete_propagate": t.delete_propagate,
                    "exclude": t.exclude,
                })
        return jsonify({"tasks": tasks, "path": CONFIG_PATH})

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
            return jsonify({"status": "warn", "message": f"已保存，{len(errors)} 项提醒"})
        return jsonify({"status": "ok", "message": "✓ 配置已保存"})
    except yaml.YAMLError as e:
        return jsonify({"status": "error", "message": f"YAML 错误: {e}"})


# ══════════════════════════════════════════════════════════════════
# Sync Control
# ══════════════════════════════════════════════════════════════════

def _log(msg, style=""):
    _log_queue.put((msg, style))


def _run_sync(mode="watch"):
    global _sync_status
    _sync_status = {"running": True, "task": "", "stats": {}}
    _sync_stop.clear()
    continuous = (mode == "watch")

    # Bridge Python logging → Web UI
    web_handler = _WebLogHandler()
    web_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    web_handler.setLevel(logging.INFO)
    for name in ["isync", "isync.engine", "isync.watcher", "isync.sftp"]:
        logging.getLogger(name).addHandler(web_handler)
        logging.getLogger(name).setLevel(logging.INFO)

    try:
        cfg = Config(CONFIG_PATH)
        if not cfg.tasks:
            _log("错误: 没有配置同步任务，请先到「配置」Tab 设置。", "err")
            return
        task = cfg.tasks[0]
        _sync_status["task"] = task.name

        _log(f"连接到 {task.remote_user}@{task.remote_host}:{task.remote_port} ...")
        sftp = SFTPClient(
            host=task.remote_host, port=task.remote_port,
            user=task.remote_user, auth_type=task.auth_type,
            password=task.password, ssh_key_path=task.ssh_key_path,
        )
        sftp.connect()
        _log("✓ 已连接", "up")

        engine = SyncEngine(task, sftp)

        # Initial scan
        _log("扫描本地文件...")
        local_files = engine.scan_local()
        _log(f"  本地: {len(local_files)} 个文件")

        _log("扫描远端文件...")
        remote_files = engine.scan_remote()
        _log(f"  远端: {len(remote_files)} 个文件")

        # Diff
        plan = engine.diff(local_files, remote_files)
        _log(f"对比结果: ↑{len(plan.to_upload)} 上传 ↓{len(plan.to_download)} 下载 "
             f"✗远端:{len(plan.to_delete_remote)} ✗本地:{len(plan.to_delete_local)} "
             f"跳过:{len(plan.skipped)}")

        # Execute
        if plan.total_actions > 0:
            _log("开始同步...")
            stats, up, down, dl, dr, errs = engine.execute(plan)
            for f in up: _log(f"  ↑ {f}", "up")
            for f in down: _log(f"  ↓ {f}", "down")
            for f in dr: _log(f"  ✗ 远端 {f}", "err")
            for f in dl: _log(f"  ✗ 本地 {f}", "err")
            for e in errs: _log(f"  ✖ {e['path']}: {e['error']}", "err")
            _sync_status["stats"] = {
                "uploaded": stats["uploaded"], "downloaded": stats["downloaded"],
                "skipped": stats["skipped"], "errors": stats["errors"],
            }
            _log(f"✓ 同步完成: ↑{stats['uploaded']} ↓{stats['downloaded']} "
                 f"跳过:{stats['skipped']} 错误:{stats['errors']}")
        else:
            _log("✓ 文件已一致，无需同步")
            _sync_status["stats"] = {"uploaded": 0, "downloaded": 0, "skipped": len(plan.skipped), "errors": 0}

        # Watch mode (skip if single-pass)
        if continuous and not _sync_stop.is_set():
            _log("")
            _log("进入监控模式 — 文件变化会自动同步")

            watchers = []
            if os.path.isdir(task.local_path):
                fw = FileWatcher(task, on_change=engine.sync_single)
                fw.start()
                watchers.append(fw)
            if task.direction != "local-to-remote":
                rp = RemotePoller(engine, interval=task.poll_interval)
                rp.start()
                watchers.append(rp)

            while not _sync_stop.is_set():
                _sync_stop.wait(timeout=1)

            for w in watchers:
                try:
                    w.stop()
                except Exception:
                    pass
            _log("监控已停止")

        sftp.disconnect()

    except ConnectionError as e:
        _log(f"连接失败: {e}", "err")
    except Exception as e:
        _log(f"同步错误: {e}", "err")
    finally:
        for name in ["isync", "isync.engine", "isync.watcher", "isync.sftp"]:
            logging.getLogger(name).removeHandler(web_handler)
        _sync_status["running"] = False


@app.route("/api/sync/start", methods=["POST"])
def api_sync_start():
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return jsonify({"ok": False, "error": "同步已在运行中"})
    data = request.get_json() or {}
    mode = data.get("mode", "watch")  # "watch" or "once"
    _log_queue.queue.clear()
    _sync_thread = threading.Thread(target=_run_sync, args=(mode,), daemon=True)
    _sync_thread.start()
    return jsonify({"ok": True})


@app.route("/api/sync/stop", methods=["POST"])
def api_sync_stop():
    _sync_stop.set()
    _log("正在停止...", "dim")
    return jsonify({"ok": True})


@app.route("/api/sync/status")
def api_sync_status():
    return jsonify({
        "running": _sync_status["running"],
        "task": _sync_status["task"],
        "stats": _sync_status.get("stats", {}),
    })


@app.route("/api/sync/logs")
def api_sync_logs():
    lines = []
    while not _log_queue.empty():
        try:
            msg, style = _log_queue.get_nowait()
            cls = {"up": "up", "down": "down", "err": "err"}.get(style, "dim")
            lines.append(f'<span class="{cls}">{msg}</span>')
        except Exception:
            break
    return jsonify({
        "lines": "\n".join(lines),
        "running": _sync_status["running"],
        "stats": _sync_status.get("stats", {}),
    })


# ══════════════════════════════════════════════════════════════════

def run_web(config_path="config.yaml", host="127.0.0.1", port=8080):
    global CONFIG_PATH
    CONFIG_PATH = os.path.expanduser(config_path)
    print(f"\n  iSync → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
