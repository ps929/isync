#!/usr/bin/env python3
"""TUI Demo — 完全本地，无需网络。"""
import os, sys, tempfile, shutil, time

sys.path.insert(0, os.path.dirname(__file__))
from config import SyncTask
from sync_engine import SyncEngine
from sftp_client import FileInfo
from display import SyncDisplay


class LocalMockSftp:
    def __init__(self, base): self.base = base
    def list_files(self, path, exclude=None):
        result = {}
        for dirpath, dirnames, filenames in os.walk(self.base):
            for f in filenames:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, self.base)
                s = os.stat(full)
                result[rel] = FileInfo(path=rel, size=s.st_size, mtime=s.st_mtime)
        return result
    def upload(self, src, dst, callback=None):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        size = os.path.getsize(src)
        chunk = max(size // 20, 1024)
        for done in range(chunk, size + chunk, chunk):
            done = min(done, size)
            time.sleep(0.03)
            if callback: callback(done, size)
        shutil.copy2(src, dst)
    def download(self, src, dst, callback=None):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        size = os.path.getsize(src)
        chunk = max(size // 20, 1024)
        for done in range(chunk, size + chunk, chunk):
            done = min(done, size)
            time.sleep(0.03)
            if callback: callback(done, size)
        shutil.copy2(src, dst)
    def delete(self, p):
        if os.path.isfile(p): os.remove(p)
    def get_info(self, p):
        if os.path.isfile(p):
            s = os.stat(p)
            return FileInfo(path=os.path.basename(p), size=s.st_size, mtime=s.st_mtime)
        return None
    def connect(self): pass
    def disconnect(self): pass
    @property
    def is_connected(self): return True
    def _ensure_connected(self): pass


tmp = tempfile.mkdtemp(prefix='isync_demo_')
local_dir = os.path.join(tmp, 'local')
remote_dir = os.path.join(tmp, 'remote')
os.makedirs(local_dir); os.makedirs(remote_dir)

print("准备测试文件...", end=" ", flush=True)

sizes_kb = [5, 30, 150, 600, 2500, 50, 400, 1200]
for i, kb in enumerate(sizes_kb):
    with open(os.path.join(local_dir, f'local_file_{i:02d}.dat'), 'wb') as f:
        f.write(os.urandom(kb * 1024))

remote_sizes = [20, 300, 1000, 80, 2000]
for i, kb in enumerate(remote_sizes):
    with open(os.path.join(remote_dir, f'remote_file_{i:02d}.dat'), 'wb') as f:
        f.write(os.urandom(kb * 1024))

for i in range(3):
    data = f'shared content {i}\n' * 100
    with open(os.path.join(local_dir, f'synced_{i}.txt'), 'w') as f: f.write(data)
    with open(os.path.join(remote_dir, f'synced_{i}.txt'), 'w') as f: f.write(data)

print("完成")
print()

task = SyncTask(name='my-sync', local_path=local_dir, remote_host='demo',
                remote_user='pansong', remote_path=remote_dir,
                direction='bidirectional', conflict_resolution='newer')

sftp = LocalMockSftp(remote_dir)
display = SyncDisplay(task_name='my-sync', enabled=True)

with display:
    time.sleep(0.2)
    engine = SyncEngine(task, sftp, display=display)
    engine.sync()
    time.sleep(0.8)

print()
print("✅ Demo 完成")
shutil.rmtree(tmp)
