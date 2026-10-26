"""Temporary dashboard workspace: ONE active upload batch, replaced wholesale by the next one.

No permanent storage exists here. Old batch ids and old file ids are refused, and only files that
were fully parsed and profiled are ever registered.
"""
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shared.cleanup import MARKER, mark_owned, purge_owned_dir
from shared.safe_files import resolve_within, save_upload

from . import config
from .profile import DataError, load_file, profile_df

BATCH = re.compile(r"^[0-9a-f]{32}$")


class BatchError(ValueError):
    """The batch or file is not active. Message is safe to show."""


class WorkspaceError(RuntimeError):
    """The workspace folder is unsafe to use."""


@dataclass
class Dataset:
    file_id: str
    name: str
    kind: str
    size: int
    skipped: list = field(default_factory=list)
    tables: dict = field(default_factory=dict)      # sheet -> {"df", "header_row", "profile"}

    def public(self):
        return {"id": self.file_id, "name": self.name, "kind": self.kind, "size": self.size,
                "skipped_sheets": self.skipped,
                "tables": [dict(t["profile"], sheet=s, header_row=t["header_row"]) for s, t in self.tables.items()]}


class Workspace:
    def __init__(self, root):
        root = Path(root)
        if root.exists() and any(root.iterdir()) and not (root / MARKER).is_file():
            raise WorkspaceError("Refusing to use a non-empty folder that is not marked as application-owned.")
        self.root = mark_owned(root)
        self._lock = threading.RLock()
        self._batch = None
        self._data = {}
        purge_owned_dir(self.root)                  # leftovers from a previous run

    def _check(self, batch):
        if not isinstance(batch, str) or not BATCH.match(batch) or batch != self._batch:
            raise BatchError("This upload batch is no longer active. Start a new upload.")

    def new_batch(self):
        """Replace the active dataset. Removes only this workspace's own temporary files."""
        with self._lock:
            purge_owned_dir(self.root)
            self._data = {}
            self._batch = uuid.uuid4().hex
            mark_owned(self.root / self._batch)
            return self._batch

    def clear(self):
        with self._lock:
            purge_owned_dir(self.root)
            self._data, self._batch = {}, None

    def add_upload(self, batch, fileobj, name):
        with self._lock:
            self._check(batch)
            if len(self._data) >= config.MAX_FILES:
                raise BatchError(f"A batch can hold at most {config.MAX_FILES} files.")
        try:
            info = save_upload(fileobj, name, resolve_within(self.root, batch),
                               config.ALLOWED_KINDS, config.MAX_UPLOAD_BYTES)
        except OSError as exc:
            raise BatchError("The upload was interrupted because a new batch was started.") from exc
        path = info["stored_path"]
        try:
            loaded, skipped = load_file(path, info["kind"])
            tables = {t["sheet"]: {"df": t["df"], "header_row": t["header_row"], "profile": profile_df(t["df"])}
                      for t in loaded}
            ds = Dataset(info["id"], info["display_name"], info["kind"], info["size"], skipped, tables)
            with self._lock:
                self._check(batch)                  # a new batch may have started while we parsed
                if len(self._data) >= config.MAX_FILES:
                    raise BatchError(f"A batch can hold at most {config.MAX_FILES} files.")
                self._data[ds.file_id] = ds
            return ds
        except (DataError, BatchError):
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            path.unlink(missing_ok=True)
            raise DataError("The file could not be analysed.") from exc

    def files(self, batch):
        with self._lock:
            self._check(batch)
            return [d.public() for d in self._data.values()]

    def get_table(self, batch, file_id, sheet):
        """A COPY of one table of the active batch. Anything else raises."""
        with self._lock:
            self._check(batch)
            ds = self._data.get(file_id)
            if ds is None:
                raise BatchError("Unknown file for this batch.")
            t = ds.tables.get(sheet)
            if t is None:
                raise BatchError("Unknown sheet.")
            return t["df"].copy()