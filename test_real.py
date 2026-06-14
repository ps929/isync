"""Real Windows remote — comprehensive system test. 15 scenarios."""
import sys, os, time, tempfile, shutil
sys.path.insert(0, '.')
from config import Config
from sftp_client import SFTPClient
from syncer import Syncer
import logging
logging.basicConfig(level=logging.WARN, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
import builtins
_print = builtins.print
def P(*a, **k): _print(*a, **k, flush=True)

cfg = Config(); t = cfg.tasks[0]
L = t.local_path; R = t.remote_path.rstrip('/')
sftp = SFTPClient(t.remote_host, t.remote_port, t.remote_user, t.auth_type, t.password, t.ssh_key_path)
P('连接...'); sftp.connect(); print('已连接')
syncer = Syncer(t, sftp)

passed = 0; failed = 0
def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1; print(f'  ✅ {name} {detail}')
    else:
        failed += 1; print(f'  ❌ {name} FAIL {detail}')

def clean():
    """Remove test artifacts from both sides."""
    for f in ['_test_file.txt','_test_mod.txt','_test_del.txt','_test_chi.txt',
              '_test_char.txt','_test_large.bin','_test_remote.txt','_test_rmod.txt']:
        lp = os.path.join(L, f)
        if os.path.isfile(lp): os.remove(lp)
        try: sftp.delete(f'{R}/{f}')
        except: pass
    for d in ['_test_dir','_test_dir2','_test_nest','_test_empty']:
        lp = os.path.join(L, d)
        if os.path.isdir(lp): shutil.rmtree(lp, ignore_errors=True)
        try:
            files, dirs = sftp.list_files(f'{R}/{d}')
            for f in files: sftp.delete(f'{R}/{d}/{f}')
            for sd in sorted(dirs, key=lambda x: -x.count('/')):
                sftp.rmdir(f'{R}/{d}/{sd}')
            sftp.rmdir(f'{R}/{d}')
        except: pass

# ══════════════════════════════════════════════════════════════════
P('\n=== 1. Initial sync (baseline) ===')
clean()
syncer = Syncer(t, sftp); syncer.initial_sync()
st = syncer.db.stats()
check('Index created', st['local'] > 0 and st['remote'] > 0, f'local={st["local"]} remote={st["remote"]}')

P('\n=== 2. Local file create → remote ===')
with open(os.path.join(L, '_test_file.txt'), 'w') as f: f.write('hello windows')
syncer.sync_local_change('_test_file.txt', 'created')
rf, _ = sftp.list_files(R)
check('File on remote', '_test_file.txt' in rf)

P('\n=== 3. Local file modify → remote ===')
time.sleep(0.5)
with open(os.path.join(L, '_test_file.txt'), 'w') as f: f.write('modified on mac')
syncer.sync_local_change('_test_file.txt', 'modified')
# Verify on remote
tmp = '/tmp/_verify_mod.txt'
sftp.download(f'{R}/_test_file.txt', tmp)
with open(tmp) as f: content = f.read()
check('Content matches', content == 'modified on mac', content)
os.remove(tmp)

P('\n=== 4. Local file delete → remote ===')
os.remove(os.path.join(L, '_test_file.txt'))
syncer.sync_local_change('_test_file.txt', 'deleted')
rf, _ = sftp.list_files(R)
check('File gone from remote', '_test_file.txt' not in rf)

P('\n=== 5. Remote file create → local ===')
with open(os.path.join(L, '_test_remote.txt'), 'w') as f: f.write('from mac')
syncer.sync_local_change('_test_remote.txt', 'created')  # pre-condition
# Now create directly on remote via SFTP write
with sftp._sftp.open(f'{R}/_test_remote2.txt', 'w') as f:
    f.write('hello from windows')
syncer.poll_remote()
check('File on local', os.path.isfile(os.path.join(L, '_test_remote2.txt')))

P('\n=== 6. Remote file modify → local ===')
time.sleep(0.5)
with sftp._sftp.open(f'{R}/_test_remote2.txt', 'w') as f:
    f.write('modified on windows')
syncer.poll_remote()
with open(os.path.join(L, '_test_remote2.txt')) as f:
    check('Content matches', f.read() == 'modified on windows')

P('\n=== 7. Remote file delete → local ===')
sftp.delete(f'{R}/_test_remote2.txt')
syncer.poll_remote()
check('File gone from local', not os.path.isfile(os.path.join(L, '_test_remote2.txt')))

P('\n=== 8. Local directory create → remote ===')
os.makedirs(os.path.join(L, '_test_dir'), exist_ok=True)
syncer.sync_local_change('_test_dir', 'created')
_, rd = sftp.list_files(R)
check('Dir on remote', '_test_dir' in rd)

P('\n=== 9. Local directory delete → remote ===')
shutil.rmtree(os.path.join(L, '_test_dir'))
syncer.sync_local_change('_test_dir', 'deleted')
_, rd = sftp.list_files(R)
check('Dir gone from remote', '_test_dir' not in rd)

P('\n=== 10. Nested directory sync ===')
# Create nested on Mac
os.makedirs(os.path.join(L, '_test_nest/a/b/c'), exist_ok=True)
with open(os.path.join(L, '_test_nest/a/b/c/f.txt'), 'w') as f: f.write('deep')
syncer.sync_local_change('_test_nest/a/b/c/f.txt', 'created')
rf, rd = sftp.list_files(R)
check('Nested file on remote', '_test_nest/a/b/c/f.txt' in rf)
shutil.rmtree(os.path.join(L, '_test_nest'))
time.sleep(0.3)
syncer.sync_local_change('_test_nest', 'deleted')
_, rd = sftp.list_files(R)
check('Nested dir gone', '_test_nest' not in rd)

P('\n=== 11. Large file (1MB) ===')
data = os.urandom(1024*1024)
with open(os.path.join(L, '_test_large.bin'), 'wb') as f: f.write(data)
t0 = time.time()
syncer.sync_local_change('_test_large.bin', 'created')
elapsed = time.time() - t0
rf, _ = sftp.list_files(R)
check('Large file on remote', '_test_large.bin' in rf, f'{elapsed:.1f}s')
os.remove(os.path.join(L, '_test_large.bin'))
sftp.delete(f'{R}/_test_large.bin')

P('\n=== 12. Single character change ===')
with open(os.path.join(L, '_test_char.txt'), 'w') as f: f.write('abcdef')
syncer.sync_local_change('_test_char.txt', 'created')
time.sleep(0.5)
with open(os.path.join(L, '_test_char.txt'), 'w') as f: f.write('abcDef')
syncer.sync_local_change('_test_char.txt', 'modified')
tmp = '/tmp/_verify_char2.txt'
sftp.download(f'{R}/_test_char.txt', tmp)
with open(tmp) as f: check('Char change synced', f.read() == 'abcDef')
os.remove(tmp); os.remove(os.path.join(L, '_test_char.txt'))
sftp.delete(f'{R}/_test_char.txt')

P('\n=== 13. Chinese filename ===')
with open(os.path.join(L, '_test_中文.txt'), 'w') as f: f.write('中文测试')
syncer.sync_local_change('_test_中文.txt', 'created')
rf, _ = sftp.list_files(R)
check('Chinese file on remote', '_test_中文.txt' in rf)
os.remove(os.path.join(L, '_test_中文.txt'))
sftp.delete(f'{R}/_test_中文.txt')

P('\n=== 14. .isync.db excluded ===')
# The db files should NOT be uploaded even if they change
rf_before = set(sftp.list_files(R)[0].keys())
check('No .isync.db on remote', '.isync.db' not in rf_before)

P('\n=== 15. Empty directory sync ===')
os.makedirs(os.path.join(L, '_test_empty'), exist_ok=True)
syncer.sync_local_change('_test_empty', 'created')
time.sleep(1)
syncer.poll_remote()
os.rmdir(os.path.join(L, '_test_empty'))
time.sleep(0.3)
syncer.sync_local_change('_test_empty', 'deleted')
_, rd = sftp.list_files(R)
check('Empty dir deleted from remote', '_test_empty' not in rd)

# ══════════════════════════════════════════════════════════════════
P(f'\n{"="*50}')
P(f'Results: {passed} passed, {failed} failed, {passed+failed} total')
sftp.delete(f'{R}/_test_remote.txt')
clean()
syncer.close()
sftp.disconnect()
