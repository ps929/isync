"""Test: Windows → Mac via RemotePoller"""
import sys, os, time, threading
sys.path.insert(0, '.')
from config import Config
from sftp_client import SFTPClient
from sync_engine import SyncEngine
from watcher import RemotePoller

cfg = Config("config.yaml")
task = cfg.tasks[0]
sftp = SFTPClient(task.remote_host, task.remote_port, task.remote_user,
                  task.auth_type, task.password, task.ssh_key_path)
sftp.connect()
engine = SyncEngine(task, sftp)

# Create test file on Windows via SFTP
remote_test = f"{task.remote_path.rstrip('/')}/_isync_poll_test.txt"
# Write using SFTP open
with sftp._sftp.open(remote_test, 'w') as f:
    f.write(f"windows test {time.ctime()}")
print(f"1. 已在 Windows 创建: {remote_test}")

# Start RemotePoller
rp = RemotePoller(engine, interval=task.poll_interval)
# Seed with current state (without the test file)
rp._last_remote = engine.scan_remote()
# Re-create the file after seeding so it's "new"
with sftp._sftp.open(remote_test, 'w') as f:
    f.write(f"windows test v2 {time.ctime()}")
print(f"2. 重新创建文件（确保被检测为新文件）")

# Now scan to check
time.sleep(1)
current = engine.scan_remote()
print(f"3. 远端扫描: {len(current)} 个文件")

if '_isync_poll_test.txt' in current:
    print("   文件存在于远端")
    info = current['_isync_poll_test.txt']
    action = engine.resolve_remote_change('_isync_poll_test.txt', info)
    print(f"   裁决结果: {action}")
    if action == 'download':
        engine.download_file('_isync_poll_test.txt')
        local_path = os.path.join(task.local_path, '_isync_poll_test.txt')
        if os.path.isfile(local_path):
            print(f"4. ✅ 已下载到 Mac: {local_path}")
        else:
            print("4. ❌ 下载失败")
    else:
        print(f"   跳过（裁决={action}）")
else:
    print("   ❌ 文件不在远端扫描结果中")

# Cleanup
sftp.delete(remote_test)
local_test = os.path.join(task.local_path, '_isync_poll_test.txt')
if os.path.isfile(local_test):
    os.remove(local_test)
sftp.disconnect()
print("清理完成")
