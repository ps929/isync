"""iSync — Web dashboard (config + sync control)."""
import os, json, logging, threading, queue, yaml
from flask import Flask, request, jsonify, render_template_string
from config import Config
from sftp_client import SFTPClient
from syncer import Syncer
from watcher import FileWatcher, RemotePoller

logger = logging.getLogger("isync.web")
app = Flask(__name__)
CONFIG_PATH = os.path.expanduser("config.yaml")
_sync_thread = None; _sync_stop = threading.Event()
_log_queue = queue.Queue(); _sync_status = {"running": False}

class _WebHandler(logging.Handler):
    def emit(self, r):
        s = ""; m = self.format(r)
        if r.levelno >= logging.ERROR: s = "err"
        elif "↑" in m: s = "up"
        elif "↓" in m: s = "down"
        _log_queue.put((m, s))

PAGE = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>iSync</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--b:#30363d;--g:#3fb950;--r:#f85149;--a:#58a6ff;--t:#c9d1d9;--m:#8b949e}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--t);display:flex;justify-content:center;min-height:100vh}
.c{width:100%;max-width:560px;padding:20px}
.tabs{display:flex;gap:4px}.tab{padding:10px 20px;border:none;border-radius:8px 8px 0 0;cursor:pointer;font-size:14px;background:var(--card);color:var(--m)}.tab.active{background:var(--a);color:#fff}
.card{background:var(--card);border:1px solid var(--b);border-radius:0 12px 12px 12px;padding:24px}
h2{font-size:20px;margin-bottom:2px}h2 span{color:var(--g)}.sub{font-size:13px;color:var(--m);margin-bottom:20px}
.row{display:flex;gap:10px;margin-bottom:12px}.row>div{flex:1}
label{font-size:11px;font-weight:600;color:var(--m);text-transform:uppercase;letter-spacing:.3px;display:block;margin-bottom:3px}
input,select{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--b);border-radius:8px;color:var(--t);font-size:14px}
input:focus,select:focus{border-color:var(--a);outline:none}
.btn{padding:12px 24px;border:none;border-radius:8px;cursor:pointer;font-size:15px;font-weight:600;transition:all .15s}
.btn-save{width:100%;background:var(--g);color:#000;margin-top:8px}.btn-start{background:var(--g);color:#000}.btn-stop{background:var(--r);color:#fff}
.btn-group{display:flex;gap:8px;margin-top:12px}
.msg{padding:12px;border-radius:8px;margin-top:12px;font-size:13px;text-align:center}.msg.ok{background:#1b3a1b;color:var(--g)}.msg.err{background:#3a1b1b;color:var(--r)}
.console{background:#000;color:var(--g);padding:14px;border-radius:8px;height:280px;overflow-y:auto;font-family:'SF Mono',monospace;font-size:12px;line-height:1.5;margin-top:12px;white-space:pre-wrap}
.console .dim{color:var(--m)}.console .err{color:var(--r)}.console .up{color:var(--a)}.console .down{color:var(--g)}
.stats-row{display:flex;gap:10px;margin:10px 0}.stat{flex:1;background:var(--bg);padding:8px;border-radius:6px;text-align:center}.stat .n{font-size:18px;font-weight:700}.stat .l{font-size:10px;color:var(--m)}
</style></head><body>
<div class="c">
<div class="tabs">
  <button class="tab active" onclick="S('cfg').style.display='';S('sync').style.display='none';Q('.tab')[0].classList.add('active');Q('.tab')[1].classList.remove('active')">⚙ 配置</button>
  <button class="tab" onclick="S('cfg').style.display='none';S('sync').style.display='';Q('.tab')[1].classList.add('active');Q('.tab')[0].classList.remove('active');L()">🔄 同步</button>
</div>
<div id="cfg" class="card">
  <h2>iSync<span>●</span></h2><div class="sub">填好参数保存，切换到同步 Tab 启动</div>
  <div class="row"><div><label>远端 IP</label><input id="host"></div><div><label>端口</label><input id="port" type="number" value="22"></div></div>
  <div class="row"><div><label>用户名</label><input id="user"></div><div><label>密码</label><input id="pass" type="password"></div></div>
  <div class="row"><div><label>本地路径</label><input id="lpath"></div><div><label>远端路径</label><input id="rpath"></div></div>
  <div class="row"><div><label>方向</label><select id="dir"><option value="bidirectional">双向</option><option value="local-to-remote">本地→远端</option><option value="remote-to-local">远端→本地</option></select></div><div><label>轮询(秒)</label><input id="poll" type="number" value="10"></div></div>
  <button class="btn btn-save" onclick="save()">💾 保存</button><div id="msg" class="msg"></div>
</div>
<div id="sync" class="card" style="display:none">
  <h2>🔄 同步</h2><div class="sub" id="tname">my-sync</div>
  <div id="stats" class="stats-row"></div>
  <div class="btn-group"><button class="btn btn-start" id="bstart" onclick="start()">▶ 开始</button><button class="btn btn-stop" id="bstop" onclick="stop()" style="display:none">■ 停止</button></div>
  <div class="console" id="log">准备就绪。</div>
</div>
</div>
<script>
let T=null;function S(id){return document.getElementById(id)}function Q(s){return document.querySelectorAll(s)}
async function I(){let r=await fetch('/api/config');let d=await r.json();if(!d.tasks||!d.tasks.length)return;let t=d.tasks[0];S('host').value=t.remote_host||'';S('port').value=t.remote_port||22;S('user').value=t.remote_user||'';S('pass').value=t.password||'';S('lpath').value=t.local_path||'';S('rpath').value=t.remote_path||'';S('dir').value=t.direction||'bidirectional';S('poll').value=t.poll_interval||10;S('tname').textContent=t.name||'my-sync'}
async function save(){let y=['sync_tasks:','  - name: my-sync','    local_path: "'+S('lpath').value+'"','    remote_host: "'+S('host').value+'"','    remote_port: '+S('port').value,'    remote_user: "'+S('user').value+'"','    auth_type: password','    password: "'+S('pass').value+'"','    remote_path: "'+S('rpath').value+'"','    direction: '+S('dir').value,'    poll_interval: '+S('poll').value,'','global:','  log_level: INFO'].join('\n');let r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml:y})});let d=await r.json();let m=S('msg');m.className='msg '+(d.status=='ok'?'ok':'err');m.textContent=d.message}
async function start(){let r=await fetch('/api/sync/start',{method:'POST'});if(!(await r.json()).ok)return;S('bstart').style.display='none';S('bstop').style.display='';S('log').innerHTML='';T=setInterval(L,1000)}
async function stop(){await fetch('/api/sync/stop',{method:'POST'});S('bstart').style.display='';S('bstop').style.display='none';clearInterval(T)}
async function L(){let r=await fetch('/api/sync/logs');let d=await r.json();if(d.lines)S('log').innerHTML=d.lines;if(!d.running){S('bstart').style.display='';S('bstop').style.display='none';clearInterval(T)}}
I()
</script></body></html>"""

@app.route("/")
def index(): return render_template_string(PAGE)

@app.route("/api/config", methods=["GET","POST"])
def api_config():
    global CONFIG_PATH
    if request.method == "GET":
        cfg = Config(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
        tasks = []
        if cfg and cfg.tasks:
            for t in cfg.tasks:
                tasks.append({"name":t.name,"local_path":t.local_path,"remote_host":t.remote_host,
                    "remote_port":t.remote_port,"remote_user":t.remote_user,"password":t.password,
                    "remote_path":t.remote_path,"direction":t.direction,"poll_interval":t.poll_interval})
        return jsonify({"tasks":tasks})
    d = request.get_json(); yt = d.get("yaml","")
    try:
        yaml.safe_load(yt)
        tmp = CONFIG_PATH+".tmp"
        with open(tmp,"w") as f: f.write(yt)
        os.replace(tmp, CONFIG_PATH)
        return jsonify({"status":"ok","message":"✓ 已保存"})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)})

@app.route("/api/sync/start", methods=["POST"])
def api_sync_start():
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive(): return jsonify({"ok":False})
    _log_queue.queue.clear()
    _sync_thread = threading.Thread(target=_run, daemon=True)
    _sync_thread.start()
    return jsonify({"ok":True})

@app.route("/api/sync/stop", methods=["POST"])
def api_sync_stop():
    _sync_stop.set(); return jsonify({"ok":True})

@app.route("/api/sync/logs")
def api_sync_logs():
    lines = []
    while not _log_queue.empty():
        try: m,s = _log_queue.get_nowait(); c = {"up":"up","down":"down","err":"err"}.get(s,"dim"); lines.append(f'<span class="{c}">{m}</span>')
        except: break
    return jsonify({"lines":"\n".join(lines),"running":_sync_status["running"]})

def _run():
    global _sync_status
    _sync_status = {"running":True}; _sync_stop.clear()
    h = _WebHandler(); h.setFormatter(logging.Formatter("%(asctime)s %(message)s","%H:%M:%S")); h.setLevel(logging.INFO)
    for n in ["isync","isync.sftp","isync.syncer","isync.watcher"]:
        logging.getLogger(n).addHandler(h); logging.getLogger(n).setLevel(logging.INFO)
    try:
        cfg = Config(CONFIG_PATH)
        t = cfg.tasks[0]
        sftp = SFTPClient(t.remote_host,t.remote_port,t.remote_user,t.auth_type,t.password,t.ssh_key_path)
        sftp.connect()
        syncer = Syncer(t, sftp); syncer.initial_sync()
        w = []; w.append(FileWatcher(t.local_path,t.exclude,syncer.sync_local_change)); w[0].start()
        if t.direction != "local-to-remote":
            rp = RemotePoller(syncer, t.poll_interval); rp.start(); w.append(rp)
        while not _sync_stop.is_set(): _sync_stop.wait(5)
        for x in w:
            try: x.stop()
            except: pass
        syncer.close(); sftp.disconnect()
    except Exception as e:
        _log_queue.put((f"错误: {e}","err"))
    finally:
        for n in ["isync","isync.sftp","isync.syncer","isync.watcher"]: logging.getLogger(n).removeHandler(h)
        _sync_status["running"] = False

def run_web(path="config.yaml", host="127.0.0.1", port=8080):
    global CONFIG_PATH; CONFIG_PATH = os.path.expanduser(path)
    print(f"\n  iSync → http://{host}:{port}\n"); app.run(host=host,port=port,debug=False)
