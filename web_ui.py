"""
iSync — Simple Web UI
Run with: python3 main.py web
"""

import os, json, logging, yaml
from flask import Flask, request, jsonify, render_template_string

from config import Config

logger = logging.getLogger("isync.web")
app = Flask(__name__)
CONFIG_PATH = os.path.expanduser("config.yaml")

PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>iSync</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--text:#c9d1d9;--muted:#8b949e}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);display:flex;justify-content:center;align-items:center;min-height:100vh}
.container{width:100%;max-width:520px;padding:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px}
.card h2{font-size:20px;margin-bottom:4px}.card h2 span{color:var(--green)}
.card .sub{font-size:13px;color:var(--muted);margin-bottom:24px}
.row{display:flex;gap:10px;margin-bottom:14px}
.row.col2>div{flex:1}
label{display:block;font-size:12px;font-weight:600;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.3px}
input,select{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:15px}
input:focus{border-color:var(--accent);outline:none}
input.large{padding:14px 12px;font-size:18px}
.btn{width:100%;padding:14px;border:none;border-radius:8px;cursor:pointer;font-size:16px;font-weight:600;transition:all .15s;margin-top:8px}
.btn-save{background:var(--green);color:#000}.btn-save:hover{opacity:.9}
.msg{padding:12px;border-radius:8px;margin-top:12px;font-size:13px;text-align:center}
.msg.ok{background:#1b3a1b;color:var(--green)}
.msg.err{background:#3a1b1b;color:var(--red)}
.msg.info{background:#1a2a3a;color:var(--accent)}
.advanced-toggle{font-size:12px;color:var(--muted);cursor:pointer;margin-top:16px;text-align:center}
.advanced{display:none;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)}
.advanced.show{display:block}
.footer{text-align:center;margin-top:16px;font-size:11px;color:var(--muted)}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <h2>iSync<span>●</span></h2>
    <div class="sub">SSH 文件同步 — 只需填 4 项即可开始</div>

    <div class="row col2">
      <div><label>远端 IP</label><input id="host" placeholder="192.168.1.100"></div>
      <div><label>端口</label><input id="port" type="number" value="22"></div>
    </div>

    <label>同步路径</label>
    <input id="local_path" placeholder="/Users/pansong/Documents/sync" style="margin-bottom:14px">

    <div class="row col2">
      <div><label>登录用户名</label><input id="user" placeholder="root"></div>
      <div><label>登录密码</label><input id="password" type="password" placeholder="SSH 密码"></div>
    </div>

    <label>远端路径</label>
    <input id="remote_path" placeholder="/home/pansong/sync" style="margin-bottom:14px">

    <button class="btn btn-save" onclick="save()">💾 保存配置</button>
    <div id="msg"></div>

    <div class="advanced-toggle" onclick="document.getElementById('adv').classList.toggle('show')">⚙ 高级选项 ▾</div>
    <div class="advanced" id="adv">
      <div class="row col2">
        <div><label>同步方向</label><select id="direction"><option value="bidirectional">双向</option><option value="local-to-remote">本地→远端</option><option value="remote-to-local">远端→本地</option></select></div>
        <div><label>冲突处理</label><select id="conflict"><option value="newer">谁新谁赢</option><option value="local">本地优先</option><option value="remote">远端优先</option></select></div>
      </div>
      <div class="row col2">
        <div><label>轮询间隔 (秒)</label><input id="poll" type="number" value="30"></div>
        <div><label>删除传播</label><select id="delete_prop"><option value="yes">是</option><option value="no">否</option></select></div>
      </div>
      <div class="row col2">
        <div><label>实时监控</label><select id="watch"><option value="yes">是</option><option value="no">否</option></select></div>
        <div><label>比对方式</label><select id="comparison"><option value="mtime">时间+大小</option><option value="content">内容哈希</option></select></div>
      </div>
      <label>排除 (每行一项)</label>
      <textarea id="exclude" rows="4" style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;font-family:monospace">*.tmp
.git/**
node_modules/**
__pycache__/**
*.pyc
.DS_Store</textarea>
    </div>
  </div>
  <div class="footer">配置将保存到 {{ config_path }}</div>
</div>

<script>
async function init(){
  try{
    const r=await fetch('/api/config');const d=await r.json();
    if(!d.tasks||!d.tasks.length)return;
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
  }catch(e){}
}

async function save(){
  const msg=document.getElementById('msg');
  try{
    const task={
      name:'my-sync',
      local_path:document.getElementById('local_path').value.trim(),
      remote_host:document.getElementById('host').value.trim(),
      remote_port:parseInt(document.getElementById('port').value)||22,
      remote_user:document.getElementById('user').value.trim(),
      auth_type:'password',
      password:document.getElementById('password').value,
      remote_path:document.getElementById('remote_path').value.trim(),
      direction:document.getElementById('direction').value,
      conflict_resolution:document.getElementById('conflict').value,
      comparison:document.getElementById('comparison').value,
      poll_interval:parseInt(document.getElementById('poll').value)||30,
      watch:document.getElementById('watch').value==='yes',
      delete_propagate:document.getElementById('delete_prop').value==='yes',
      exclude:document.getElementById('exclude').value.split('\n').map(s=>s.trim()).filter(s=>s)
    };
    const yamlText = [
      'sync_tasks:',
      '  - name: "'+task.name+'"',
      '    local_path: "'+task.local_path+'"',
      '    remote_host: "'+task.remote_host+'"',
      '    remote_port: '+task.remote_port,
      '    remote_user: "'+task.remote_user+'"',
      '    auth_type: password',
      '    password: "'+task.password+'"',
      '    remote_path: "'+task.remote_path+'"',
      '    direction: '+task.direction,
      '    conflict_resolution: '+task.conflict_resolution,
      '    comparison: '+task.comparison,
      '    watch: '+(task.watch?'true':'false'),
      '    poll_interval: '+task.poll_interval,
      '    delete_propagate: '+(task.delete_propagate?'true':'false'),
      '    exclude:',
      ...task.exclude.map(e=>'      - "'+e+'"'),
      '',
      'global:',
      '  log_level: INFO',
      '  log_file: ""',
      '  max_clock_skew: 300',
      '  sync_log_dir: ""',
      '  sync_log_max_files: 500',
      '  sync_log_max_days: 30',
    ].join('\n');
    const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml:yamlText})});
    const d=await r.json();
    msg.className='msg '+(d.status==='ok'?'ok':d.status==='warn'?'info':'err');
    msg.textContent=d.message;
  }catch(e){
    msg.className='msg err';msg.textContent=e.message;
  }
}

init();
</script>
</body>
</html>"""

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
            return jsonify({"status": "warn", "message": f"已保存，{len(errors)} 项提醒: " + "; ".join(errors)})
        return jsonify({"status": "ok", "message": "✓ 配置已保存"})
    except yaml.YAMLError as e:
        return jsonify({"status": "error", "message": f"YAML 语法错误: {e}"})


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json()
    yaml_text = data.get("yaml", "")
    try:
        parsed = yaml.safe_load(yaml_text)
        if parsed is None:
            return jsonify({"valid": False, "errors": ["YAML 为空"]})
        import tempfile
        tmp = tempfile.mktemp(suffix=".yaml")
        with open(tmp, "w") as f: f.write(yaml_text)
        cfg = Config(tmp)
        errors = cfg.validate()
        os.unlink(tmp)
        if errors:
            return jsonify({"valid": False, "errors": errors})
        return jsonify({"valid": True, "message": "✓ 配置有效"})
    except yaml.YAMLError as e:
        return jsonify({"valid": False, "errors": [f"YAML 错误: {e}"]})


def run_web(config_path="config.yaml", host="127.0.0.1", port=8080):
    global CONFIG_PATH
    CONFIG_PATH = os.path.expanduser(config_path)
    print(f"\n  iSync Web UI → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
