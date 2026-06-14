"""iSync — File system scanner. Walks directories and computes block hashes."""
import os, fnmatch, logging
from typing import Dict, List
from index_db import compute_blocks

logger = logging.getLogger("isync.scanner")

def scan_local(root: str, exclude: List[str],
               block_size: int = 131072, hash_files: bool = True) -> Dict[str, dict]:
    """Recursively scan a local directory. Returns {rel_path: {size, mtime, block_hash, block_count}}."""
    result = {}
    if not os.path.isdir(root):
        return result
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _excluded(
            os.path.relpath(os.path.join(dirpath, d), root), exclude)]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if _excluded(rel, exclude):
                continue
            st = os.stat(full)
            info = {"size": st.st_size, "mtime": st.st_mtime, "block_hash": "", "block_count": 0}
            if hash_files and st.st_size > 0:
                try:
                    hashes, combined = compute_blocks(full, block_size)
                    info["block_hash"] = combined
                    info["block_count"] = len(hashes)
                except Exception as e:
                    logger.debug("Skip hash %s: %s", rel, e)
            result[rel] = info
    return result

def _excluded(rel: str, patterns: List[str]) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p):
            return True
    return False
