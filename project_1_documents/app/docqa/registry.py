"""Permanent repository registry: discovery and change detection (size, mtime, SHA-256).

The browser never supplies paths. It supplies ids such as "repo:sales.csv".
"""
import hashlib
import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from shared.cleanup import MARKER, CleanupError
from shared.safe_files import EXT_TO_KIND, resolve_within, safe_filename, sniff_kind

IGNORED_SUFFIXES = (".part", ".tmp")
MAX_SELECTION = 20


class SelectionError(ValueError):
    """A requested file is unknown, stale or not allowed. Message is safe to show."""


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    name: str
    kind: str
    size: int
    mtime_ns: int
    sha256: str
    version: int
    path: Path
    source: str  # "repository" or "session"

    def public(self):
        """What the browser may see: no server path."""
        return {"id": self.file_id, "name": self.name, "kind": self.kind, "size": self.size,
                "sha256": self.sha256[:12], "version": self.version, "source": self.source}


@dataclass
class ScanResult:
    records: list = field(default_factory=list)    # usable files, sorted by name
    new: list = field(default_factory=list)
    modified: list = field(default_factory=list)   # includes replaced files
    unchanged: list = field(default_factory=list)
    removed: list = field(default_factory=list)    # names
    invalid: list = field(default_factory=list)    # names: content does not match extension
    busy: list = field(default_factory=list)       # names: locked or still being written


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _rec(name, kind, size, mtime_ns, sha, version, path):
    return FileRecord("repo:" + name, name, kind, size, mtime_ns, sha, version, Path(path), "repository")


def check_ids(file_ids):
    if not isinstance(file_ids, (list, tuple)) or not file_ids:
        raise SelectionError("Select at least one file.")
    if len(file_ids) > MAX_SELECTION:
        raise SelectionError(f"Too many files selected (limit {MAX_SELECTION}).")
    for fid in file_ids:
        if not isinstance(fid, str):
            raise SelectionError("Invalid file selection.")


class FileRegistry:
    def __init__(self, repo_dir, db_path, allowed_kinds):
        self.repo_dir = Path(repo_dir)
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.allowed = set(allowed_kinds)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, kind TEXT, "
                         "size INTEGER, mtime_ns INTEGER, sha256 TEXT, version INTEGER)")
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ---- discovery -------------------------------------------------------
    def scan(self) -> ScanResult:
        with self._lock:
            res = ScanResult()
            stored = {r[0]: r for r in self._db.execute(
                "SELECT name,kind,size,mtime_ns,sha256,version FROM files")}
            try:
                entries = list(os.scandir(self.repo_dir))
            except FileNotFoundError:
                entries = []
            seen = set()
            for e in sorted(entries, key=lambda x: x.name.lower()):
                name = e.name
                if name.startswith(".") or name.lower().endswith(IGNORED_SUFFIXES):
                    continue
                try:
                    if not e.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                expected = EXT_TO_KIND.get(Path(name).suffix.lower())
                if expected is None or expected not in self.allowed:
                    continue
                seen.add(name)
                row = stored.get(name)
                try:
                    st = e.stat(follow_symlinks=False)
                    if row and row[2] == st.st_size and row[3] == st.st_mtime_ns:
                        rec = _rec(name, row[1], st.st_size, st.st_mtime_ns, row[4], row[5], e.path)
                        res.unchanged.append(rec)
                        res.records.append(rec)
                        continue
                    sha = _sha256(e.path)
                    st2 = os.stat(e.path)
                    if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns:
                        res.busy.append(name)       # changed while hashing: still being written
                        continue
                    kind = sniff_kind(Path(e.path))
                except OSError:
                    res.busy.append(name)           # locked or unreadable right now
                    continue
                if kind != expected:
                    res.invalid.append(name)
                    if row:
                        self._db.execute("DELETE FROM files WHERE name=?", (name,))
                    continue
                if row is None:
                    version = 1
                    self._db.execute("INSERT INTO files VALUES (?,?,?,?,?,?)",
                                     (name, kind, st.st_size, st.st_mtime_ns, sha, version))
                    bucket = res.new
                elif row[4] == sha:                 # touched only: same content
                    version = row[5]
                    self._db.execute("UPDATE files SET size=?, mtime_ns=? WHERE name=?",
                                     (st.st_size, st.st_mtime_ns, name))
                    bucket = res.unchanged
                else:                               # edited or replaced
                    version = row[5] + 1
                    self._db.execute("UPDATE files SET kind=?, size=?, mtime_ns=?, sha256=?, version=? WHERE name=?",
                                     (kind, st.st_size, st.st_mtime_ns, sha, version, name))
                    bucket = res.modified
                rec = _rec(name, kind, st.st_size, st.st_mtime_ns, sha, version, e.path)
                bucket.append(rec)
                res.records.append(rec)
            for name in stored:
                if name not in seen:
                    self._db.execute("DELETE FROM files WHERE name=?", (name,))
                    res.removed.append(name)
            self._db.commit()
            res.records.sort(key=lambda r: r.name.lower())
            return res

    # ---- selection -------------------------------------------------------
    def resolve(self, file_ids):
        """Rescan, then return records for EXACTLY the requested ids. Unknown ids raise."""
        check_ids(file_ids)
        by_id = {r.file_id: r for r in self.scan().records}
        out, seen = [], set()
        for fid in file_ids:
            if fid in seen:
                continue
            seen.add(fid)
            if fid not in by_id:
                raise SelectionError(f"File not available (removed, changed or invalid): {fid[:80]}")
            out.append(by_id[fid])
        return out

    # ---- explicit user actions -------------------------------------------
    def save_copy(self, src_path, display_name):
        """Copy a file into the repository under a safe, unused name. Never overwrites."""
        clean = safe_filename(display_name)
        stem, ext = Path(clean).stem, Path(clean).suffix
        with self._lock:
            for n in range(1, 1000):
                name = clean if n == 1 else f"{stem} ({n}){ext}"
                target = resolve_within(self.repo_dir, name)
                if not target.exists():
                    break
            else:
                raise SelectionError("Could not find a free file name.")
            part = resolve_within(self.repo_dir, name + ".part")
            try:
                shutil.copyfile(src_path, part)
                os.replace(part, target)
            finally:
                if part.exists():
                    part.unlink()
        return name

    def delete(self, file_id):
        """Explicitly delete ONE repository file. The only code path that deletes repository files."""
        with self._lock:
            rec = next((r for r in self.scan().records if r.file_id == file_id), None)
            if rec is None:
                raise SelectionError("File not found in the repository.")
            resolve_within(self.repo_dir, rec.name).unlink()
            self.scan()
            return rec.name


def prune_derived(extracted_dir, live_shas):
    """Delete derived files (named <sha256>.<ext>) whose source no longer exists. Marked folders only."""
    d = Path(extracted_dir)
    if not (d / MARKER).is_file():
        raise CleanupError(f"Refusing to prune '{d}': not marked as application-owned.")
    removed = 0
    for entry in os.scandir(d):
        if entry.name == MARKER:
            continue
        if entry.name.split(".")[0] in live_shas:
            continue
        p = Path(entry.path)
        if entry.is_symlink() or entry.is_file(follow_symlinks=False):
            p.unlink()
        else:
            shutil.rmtree(p)
        removed += 1
    return removed