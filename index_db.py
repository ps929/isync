"""iSync — Index database (SQLite). Tracks files and block hashes."""
import os, sqlite3, time, hashlib, logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("isync.index")

BLOCK_SIZE = 131072  # 128KB

class IndexDB:
    """Per-task SQLite index tracking local + remote file state."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT NOT NULL,
                side TEXT NOT NULL,  -- 'local' or 'remote'
                size INTEGER,
                mtime REAL,
                block_hash TEXT,     -- combined hash of all blocks
                block_count INTEGER DEFAULT 0,
                updated_at REAL,
                PRIMARY KEY (path, side)
            );
            CREATE TABLE IF NOT EXISTS blocks (
                file_path TEXT NOT NULL,
                side TEXT NOT NULL,
                block_index INTEGER NOT NULL,
                sha256 TEXT,
                PRIMARY KEY (file_path, side, block_index)
            );
        """)
        self.conn.commit()

    # ── file operations ──────────────────────────────────────────

    def set_files(self, side: str, files: Dict[str, dict]):
        """Replace all files for one side."""
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE side=?", (side,))
            self.conn.execute("DELETE FROM blocks WHERE side=?", (side,))
            now = time.time()
            for path, info in files.items():
                self.conn.execute(
                    "INSERT INTO files(path,side,size,mtime,block_hash,block_count,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (path, side, info["size"], info["mtime"], info.get("block_hash", ""),
                     info.get("block_count", 0), now))

    def get_files(self, side: str) -> Dict[str, dict]:
        """Return {path: {size, mtime, block_hash, block_count}}."""
        rows = self.conn.execute(
            "SELECT path,size,mtime,block_hash,block_count FROM files WHERE side=?",
            (side,)).fetchall()
        return {r[0]: {"size": r[1], "mtime": r[2], "block_hash": r[3], "block_count": r[4]}
                for r in rows}

    def update_file(self, side: str, path: str, info: dict):
        """Insert or update a single file record."""
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO files(path,side,size,mtime,block_hash,block_count,updated_at) VALUES(?,?,?,?,?,?,?)",
                (path, side, info["size"], info["mtime"], info.get("block_hash", ""),
                 info.get("block_count", 0), time.time()))

    def remove_file(self, side: str, path: str):
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE path=? AND side=?", (path, side))
            self.conn.execute("DELETE FROM blocks WHERE file_path=? AND side=?", (path, side))

    def get_file(self, side: str, path: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT size,mtime,block_hash,block_count FROM files WHERE path=? AND side=?",
            (path, side)).fetchone()
        if row:
            return {"size": row[0], "mtime": row[1], "block_hash": row[2], "block_count": row[3]}
        return None

    # ── block operations ─────────────────────────────────────────

    def get_blocks(self, side: str, path: str) -> List[str]:
        """Return list of block SHA256 hashes for a file."""
        rows = self.conn.execute(
            "SELECT sha256 FROM blocks WHERE file_path=? AND side=? ORDER BY block_index",
            (path, side)).fetchall()
        return [r[0] for r in rows if r[0]]

    def set_blocks(self, side: str, path: str, block_hashes: List[str]):
        with self.conn:
            self.conn.execute("DELETE FROM blocks WHERE file_path=? AND side=?",
                              (path, side))
            for i, h in enumerate(block_hashes):
                self.conn.execute(
                    "INSERT INTO blocks(file_path,side,block_index,sha256) VALUES(?,?,?,?)",
                    (path, side, i, h))

    # ── diff ─────────────────────────────────────────────────────

    def diff(self, source_side: str, target_side: str) -> Dict[str, list]:
        """
        Compare two sides. Returns {added, modified, deleted}
        from source perspective (what's new/changed/deleted on source vs target).
        """
        src = self.get_files(source_side)
        tgt = self.get_files(target_side)
        src_paths = set(src.keys())
        tgt_paths = set(tgt.keys())

        added = list(src_paths - tgt_paths)
        deleted = list(tgt_paths - src_paths)
        modified = []
        for p in src_paths & tgt_paths:
            if src[p]["size"] != tgt[p]["size"] or src[p]["block_hash"] != tgt[p]["block_hash"]:
                modified.append(p)

        return {"added": added, "modified": modified, "deleted": deleted}

    def close(self):
        self.conn.close()

    # ── stats ────────────────────────────────────────────────────

    def stats(self) -> dict:
        local = self.conn.execute("SELECT COUNT(*) FROM files WHERE side='local'").fetchone()[0]
        remote = self.conn.execute("SELECT COUNT(*) FROM files WHERE side='remote'").fetchone()[0]
        return {"local": local, "remote": remote}


# ── block hashing utility ─────────────────────────────────────────

def compute_blocks(filepath: str, block_size: int = BLOCK_SIZE) -> Tuple[List[str], str]:
    """Split file into blocks, return (list of SHA256, combined_hash)."""
    hashes = []
    combined = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(block_size)
            if not chunk:
                break
            h = hashlib.sha256(chunk).hexdigest()
            hashes.append(h)
            combined.update(h.encode())
    return hashes, combined.hexdigest()
