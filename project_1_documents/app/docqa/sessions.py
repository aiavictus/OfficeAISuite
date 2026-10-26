"""Temporary per-session uploads. Memory-only metadata: nothing survives an app restart."""
import re
import shutil
import threading
import uuid
from pathlib import Path

from shared.cleanup import mark_owned, purge_owned_dir
from shared.safe_files import resolve_within, save_upload

from .registry import FileRecord, SelectionError, _sha256, check_ids

SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


class SessionError(ValueError):
    pass


class SessionStore:
    def __init__(self, root, allowed_kinds, max_bytes):
        self.root = mark_owned(root)
        self.allowed = set(allowed_kinds)
        self.max_bytes = max_bytes
        self._files = {}                 # session id -> {file_id: FileRecord}
        self._lock = threading.RLock()

    def new_session(self):
        sid = uuid.uuid4().hex
        mark_owned(self.root / sid)
        with self._lock:
            self._files[sid] = {}
        return sid

    def _check(self, sid):
        if not isinstance(sid, str) or not SESSION_ID.match(sid) or sid not in self._files:
            raise SessionError("Unknown session.")

    def add_upload(self, sid, fileobj, name):
        """Validate by content, store under a random name, return a record. Raises UploadError."""
        self._check(sid)
        info = save_upload(fileobj, name, resolve_within(self.root, sid), self.allowed, self.max_bytes)
        path = info["stored_path"]
        rec = FileRecord("tmp:" + info["id"], info["display_name"], info["kind"], info["size"],
                         path.stat().st_mtime_ns, _sha256(path), 1, path, "session")
        with self._lock:
            self._files[sid][rec.file_id] = rec
        return rec

    def list_files(self, sid):
        self._check(sid)
        return sorted(self._files[sid].values(), key=lambda r: r.name.lower())

    def resolve(self, sid, file_ids):
        self._check(sid)
        out = []
        for fid in dict.fromkeys(file_ids):
            rec = self._files[sid].get(fid)
            if rec is None or not rec.path.is_file():
                raise SelectionError(f"Session file not available: {str(fid)[:80]}")
            out.append(rec)
        return out

    def save_to_repository(self, sid, file_id, registry):
        """Explicit action: copy one session file into the permanent repository."""
        self._check(sid)
        rec = self._files[sid].get(file_id)
        if rec is None:
            raise SessionError("Unknown session file.")
        return registry.save_copy(rec.path, rec.name)

    def clear(self, sid):
        """Remove this session's temporary files only. The repository is never touched."""
        self._check(sid)
        shutil.rmtree(resolve_within(self.root, sid), ignore_errors=True)
        with self._lock:
            self._files.pop(sid, None)

    def purge_all(self):
        """Startup cleanup of leftovers from a previous run."""
        removed, skipped = purge_owned_dir(self.root)
        with self._lock:
            self._files.clear()
        return removed, skipped


def resolve_selection(registry, store, sid, file_ids):
    """Map selected ids to records. Only the requested files are ever returned."""
    check_ids(file_ids)
    repo_ids = [i for i in file_ids if i.startswith("repo:")]
    tmp_ids = [i for i in file_ids if i.startswith("tmp:")]
    if len(repo_ids) + len(tmp_ids) != len(file_ids):
        raise SelectionError("Invalid file selection.")
    out = []
    if repo_ids:
        out += registry.resolve(repo_ids)
    if tmp_ids:
        out += store.resolve(sid, tmp_ids)
    return out