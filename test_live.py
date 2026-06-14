"""Test continuous sync: Mac → Windows → Mac"""
import sys, os, time, threading
sys.path.insert(0, '.')
from config import Config
from sftp_client import SFTPClient
from sync_engine import SyncEngine
from watcher import FileWatcher, RemotePoller

cfg = Config("config.yaml")
task = cfg.tasks[0]
print(f"任务: {task.name} | 方向: {task.direction} | 轮询: {task.poll_interval}s")

sftp = SFTPClient(task.remote_host, task.remote_port, task.remote_user,
                  task.auth_type, task.password, task.ssh_key_path)
sftp.connect()
print("已连接")

engine = SyncEngine(task, sftp)

# Initial sync
print("\n=== 初始同步 ===")
local = engine.scan_local()
remote = engine.scan_remote()
from sync_engine import SyncPlan
plan = engine.diff(local, remote)
print(f"本地: {len(local)} 文件, 远端: {len(remote)} 文件")
print(f"需上传: {len(plan.to_upload)}, 需下载: {len(plan.to_download)}")
engine.execute(plan)
print("初始同步完成")

# Start watchers
print("\n=== 启动监控 ===")
stop = threading.Event()

def watch_cb(path, etype):
    print(f"  [watch] {path} -> {etype}")
    engine.sync_single(path, etype)

fw = FileWatcher(task, on_change=watch_cb)
fw.start()
print("本地监控已开启")

rp = RemotePoller(engine, interval=task.poll_interval)
rp.start()
print(f"远端轮询已开启 (每 {task.poll_interval}s)")

# Test 1: Mac → Windows
print("\n=== 测试 1: Mac 创建文件 → Windows ===")
test_file = os.path.join(task.local_path, "_isync_test_mac.txt")
with open(test_file, 'w') as f:
    f.write(f"test from mac at {time.ctime()}")
print(f"已创建: {test_file}")

time.sleep(3)  # Wait for FileWatcher to pick it up

# Check Windows
remote_files = engine.scan_remote()
if '_isync_test_mac.txt' in remote_files:
    print("✅ Windows 上已出现该文件！")
else:
    print("❌ Windows 上没有该文件！")
    print(f"   远端文件数: {len(remote_files)}")

# Test 2: Windows → Mac (manual wait for poller)
print("\n=== 测试 2: 请手动操作 ===")
print("请在 Windows E:\\iNode 下创建一个文件 _isync_test_win.txt")
print("等待轮询检测...")
for i in range(int(task.poll_interval * 3)):
    time.sleep(1)
    local_now = engine.scan_local()
    if '_isync_test_win.txt' in local_now:
        print(f"✅ 第 {i+1}s: Mac 上已出现该文件！")
        break
else:
    local_now = engine.scan_local()
    if '_isync_test_win.txt' in local_now:
        print("✅ 已出现")
    else:
        print("❌ 未检测到，手动检查远端...")
        remote_now = engine.scan_remote()
        if '_isync_test_win.txt' in remote_now:
            print(f"   文件在远端存在 ({len(remote_now)} 个文件)")
            print("   但 RemotePoller 没拉过来——可能 poller 有问题")
        else:
            print(f"   文件不在远端！({len(remote_now)} 个文件)")
            print("   请确认 Windows 上真的有这个文件")

# Cleanup
os.remove(test_file)
stop.set()
rp.stop()
fw.stop()
sftp.disconnect()
print("\n测试结束")
