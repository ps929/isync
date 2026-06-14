"""Quick test: Mac → Windows via FileWatcher"""
import sys, os, time
sys.path.insert(0, '.')
from config import Config
from sftp_client import SFTPClient
from sync_engine import SyncEngine
from watcher import FileWatcher

cfg = Config("config.yaml")
task = cfg.tasks[0]
sftp = SFTPClient(task.remote_host, task.remote_port, task.remote_user,
                  task.auth_type, task.password, task.ssh_key_path)
sftp.connect()
engine = SyncEngine(task, sftp)

# Create test file on Mac
test_file = os.path.join(task.local_path, "_isync_quick_test.txt")
with open(test_file, 'w') as f:
    f.write(f"mac test {time.ctime()}")
print(f"1. 已在 Mac 创建: {test_file}")

# Upload it directly
t0 = time.time()
engine.sync_single("_isync_quick_test.txt", "created")
print(f"2. 已上传 ({time.time()-t0:.1f}s)")

# Check if it arrived on Windows
remote_files = sftp.list_files(task.remote_path)
if '_isync_quick_test.txt' in remote_files:
    print("3. ✅ Windows 上已出现该文件！上传成功")
else:
    print("3. ❌ Windows 上没有该文件")

# Cleanup
os.remove(test_file)
sftp.delete(f"{task.remote_path.rstrip('/')}/_isync_quick_test.txt")
sftp.disconnect()
print("清理完成")
