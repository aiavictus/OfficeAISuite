"""Write application\\MANIFEST.sha256 (sha256, size, relative path) for every file under application\\.

Run from the suite root after `call ENV.bat`:   python application\\scripts\\make_manifest.py
Re-run it whenever you intentionally change the suite (new model, edited config, new code).
Hashing ~20 GB of models takes several minutes.
"""
import hashlib
import os
import sys
import time
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = SUITE_ROOT / "application"
MANIFEST = APP_DIR / "MANIFEST.sha256"
EXCLUDE_DIR_NAMES = {"__pycache__", "_downloads", ".pytest_cache"}
EXCLUDE_REL_PREFIXES = ("application/logs/", "application/data/cache/")
EXCLUDE_SUFFIXES = (".part", ".pyc", ".log")


def iter_files():
    """Yield (relative posix path, absolute Path) for every manifest-eligible file."""
    for dirpath, dirnames, filenames in os.walk(APP_DIR):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(SUITE_ROOT).as_posix()
            if p == MANIFEST or fn.endswith(EXCLUDE_SUFFIXES) or rel.startswith(EXCLUDE_REL_PREFIXES):
                continue
            yield rel, p


def sha256_file(path, progress=False):
    h = hashlib.sha256()
    size = path.stat().st_size
    done, t0 = 0, time.time()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress and size > 500 * 1024 * 1024:
                print(f"\r  {path.name}: {done * 100 // size}% ({time.time() - t0:.0f}s)", end="", flush=True)
    if progress and size > 500 * 1024 * 1024:
        print()
    return h.hexdigest()


def read_manifest():
    """Return {relpath: (sha256, size)}. Raises FileNotFoundError if there is no manifest."""
    entries = {}
    for line in MANIFEST.read_text(encoding="ascii", errors="replace").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and len(parts[0]) == 64 and parts[1].isdigit():
            entries[parts[2]] = (parts[0], int(parts[1]))
    return entries


def main():
    lines, count = [], 0
    for rel, p in iter_files():
        lines.append(f"{sha256_file(p, progress=True)}  {p.stat().st_size}  {rel}")
        count += 1
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"Manifest written: {MANIFEST} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())