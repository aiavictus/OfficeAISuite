"""SQLite database discovery, validation and schema extraction.

Database files are untrusted input: they are identified by header bytes, opened read-only,
and never executed against beyond reading the schema. The browser only sends ids ("db:name").
"""
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from shared.cleanup import MARKER, CleanupError, mark_owned

from . import config

SQLITE_MAGIC = b"SQLite format 3\x00"


class SchemaError(ValueError):
    """Message is safe to show to the user."""


@dataclass(frozen=True)
class DbRecord:
    db_id: str
    name: str
    size: int
    mtime_ns: int
    sha256: str
    path: Path

    def public(self):
        return {"id": self.db_id, "name": self.name, "size": self.size, "sha256": self.sha256[:12]}


@dataclass
class ScanResult:
    records: list = field(default_factory=list)
    new: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    unchanged: list = field(default_factory=list)
    removed: list = field(default_factory=list)    # names
    invalid: list = field(default_factory=list)    # (name, reason)
    busy: list = field(default_factory=list)       # names


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sniff_sqlite(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def open_readonly(path):
    """Genuinely read-only connection: mode=ro, query_only, no extension loading."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.NotSupportedError):
        pass                                           # extension loading is not available at all
    conn.execute("PRAGMA query_only=ON")
    return conn


def validate_db(path):
    """True if the file opens read-only and its schema table can be read."""
    try:
        with closing(open_readonly(path)) as c:
            c.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return True
    except sqlite3.Error:
        return False


def extract_schema(path):
    """Tables, views, columns, types, primary keys and foreign keys from the database itself."""
    with closing(open_readonly(path)) as c:
        rows = c.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                         "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        if len(rows) > config.MAX_TABLES:
            raise SchemaError(f"The database has more than {config.MAX_TABLES} tables.")
        tables = []
        for name, kind in rows:
            cols = c.execute('SELECT name, type, "notnull", pk FROM pragma_table_info(?)', (name,)).fetchall()
            if len(cols) > config.MAX_COLUMNS_PER_TABLE:
                raise SchemaError(f"Table '{name[:60]}' has more than {config.MAX_COLUMNS_PER_TABLE} columns.")
            fks = {}
            for fid, _seq, ref, frm, to in c.execute(
                    'SELECT id, seq, "table", "from", "to" FROM pragma_foreign_key_list(?) ORDER BY id, seq', (name,)):
                fk = fks.setdefault(fid, {"from": [], "table": ref, "to": []})
                fk["from"].append(frm)
                fk["to"].append(to)
            tables.append({
                "name": name, "kind": kind,
                "columns": [{"name": n, "type": t or "", "notnull": bool(nn), "pk": int(pk)} for n, t, nn, pk in cols],
                "primary_key": [n for n, _t, _nn, pk in sorted(cols, key=lambda r: r[3]) if pk > 0],
                "foreign_keys": list(fks.values()),
            })
    by_name = {t["name"].lower(): t for t in tables}
    for t in tables:                                   # "REFERENCES customers" without columns -> primary key
        for fk in t["foreign_keys"]:
            if any(x is None for x in fk["to"]):
                ref = by_name.get(str(fk["table"]).lower())
                fk["to"] = ref["primary_key"][:len(fk["from"])] if ref else []
    return {"dialect": "sqlite", "tables": tables}


def _dict(x):
    return x if isinstance(x, dict) else {}


def apply_user_meta(schema, path):
    """Merge optional descriptions and relationships. Everything is validated against the real schema."""
    out = json.loads(json.dumps(schema))
    out["warnings"], out["declared_relationships"] = [], []
    warn = out["warnings"]
    path = Path(path)
    if not path.is_file():
        return out
    try:
        if path.stat().st_size > config.MAX_SIDECAR_BYTES:
            warn.append("Schema description file is too large and was ignored.")
            return out
        meta = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(meta, dict):
            raise ValueError
    except (OSError, ValueError):
        warn.append("Schema description file could not be read and was ignored.")
        return out
    tables = {t["name"].lower(): t for t in out["tables"]}
    if isinstance(meta.get("description"), str):
        out["description"] = meta["description"][:300]
    for tname, tmeta in _dict(meta.get("tables")).items():
        t = tables.get(str(tname).lower())
        if t is None or not isinstance(tmeta, dict):
            warn.append(f"Description for unknown table '{str(tname)[:60]}' ignored.")
            continue
        if isinstance(tmeta.get("description"), str):
            t["description"] = tmeta["description"][:300]
        cols = {c["name"].lower(): c for c in t["columns"]}
        for cname, cdesc in _dict(tmeta.get("columns")).items():
            c = cols.get(str(cname).lower())
            if c is None or not isinstance(cdesc, str):
                warn.append(f"Description for unknown column '{str(tname)[:40]}.{str(cname)[:40]}' ignored.")
                continue
            c["description"] = cdesc[:300]

    def locate(ref):
        tn, _, cn = str(ref).partition(".")
        t = tables.get(tn.lower())
        return bool(t and any(c["name"].lower() == cn.lower() for c in t["columns"]))

    rels = meta.get("relationships")
    for rel in rels if isinstance(rels, list) else []:
        if isinstance(rel, dict) and locate(rel.get("from")) and locate(rel.get("to")):
            out["declared_relationships"].append({"from": str(rel["from"]), "to": str(rel["to"])})
        else:
            warn.append("A declared relationship refers to an unknown table or column and was ignored.")
    return out


class DatabaseCatalog:
    def __init__(self, db_dir, cache_dir, schema_dir=None):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = mark_owned(cache_dir)
        self.schema_dir = Path(schema_dir) if schema_dir else None
        self._known = {}                               # name -> (size, mtime_ns, sha256)
        self._lock = threading.RLock()

    # ---- discovery -------------------------------------------------------
    def scan(self) -> ScanResult:
        with self._lock:
            res, seen = ScanResult(), set()
            try:
                entries = list(os.scandir(self.db_dir))
            except FileNotFoundError:
                entries = []
            for e in sorted(entries, key=lambda x: x.name.lower()):
                name = e.name
                if name.startswith(".") or not name.lower().endswith(config.DB_EXTENSIONS):
                    continue
                try:
                    if not e.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                seen.add(name)
                prev = self._known.get(name)
                try:
                    st = e.stat(follow_symlinks=False)
                    if st.st_size > config.MAX_DB_BYTES:
                        res.invalid.append((name, "file is too large"))
                        self._known.pop(name, None)
                        continue
                    if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
                        rec = DbRecord("db:" + name, name, st.st_size, st.st_mtime_ns, prev[2], Path(e.path))
                        res.unchanged.append(rec)
                        res.records.append(rec)
                        continue
                    sha = _sha256(e.path)
                    st2 = os.stat(e.path)
                    if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                        res.busy.append(name)           # changed while hashing: still being written
                        continue
                    if not sniff_sqlite(e.path) or not validate_db(e.path):
                        res.invalid.append((name, "not a readable SQLite database"))
                        self._known.pop(name, None)
                        continue
                except OSError:
                    res.busy.append(name)
                    continue
                rec = DbRecord("db:" + name, name, st.st_size, st.st_mtime_ns, sha, Path(e.path))
                if prev is None:
                    res.new.append(rec)
                elif prev[2] == sha:
                    res.unchanged.append(rec)
                else:
                    res.modified.append(rec)
                self._known[name] = (st.st_size, st.st_mtime_ns, sha)
                res.records.append(rec)
            for name in list(self._known):
                if name not in seen:
                    del self._known[name]
                    res.removed.append(name)
            res.records.sort(key=lambda r: r.name.lower())
            return res

    def describe(self, name):
        """Optional one-line description of a database from schemas\\<file>.json, else ''."""
        if not self.schema_dir:
            return ""
        p = self.schema_dir / (name + ".json")
        try:
            if p.is_file() and p.stat().st_size <= config.MAX_SIDECAR_BYTES:
                d = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(d, dict) and isinstance(d.get("description"), str):
                    return d["description"][:300]
        except (OSError, ValueError):
            pass
        return ""
        
    def resolve(self, db_id):
        """Rescan, then return the CURRENT DbRecord for exactly this database id."""
        if not isinstance(db_id, str) or not db_id.startswith("db:"):
            raise SchemaError("Unknown database.")
        rec = next((r for r in self.scan().records if r.db_id == db_id), None)
        if rec is None:
            raise SchemaError("Database not available (removed, changed or invalid).")
        return rec
        
    # ---- schema ----------------------------------------------------------
    def get_schema(self, db_id):
        """Rescan, then return the CURRENT schema of exactly this database. Never a stale one."""
        if not isinstance(db_id, str) or not db_id.startswith("db:"):
            raise SchemaError("Unknown database.")
        with self._lock:
            rec = next((r for r in self.scan().records if r.db_id == db_id), None)
            if rec is None:
                raise SchemaError("Database not available (removed, changed or invalid).")
            cache = self.cache_dir / f"{rec.sha256}.json"
            schema, cached = None, False
            if cache.is_file():
                try:
                    data = json.loads(cache.read_text(encoding="utf-8"))
                    if data.get("sha256") == rec.sha256:
                        schema, cached = data, True
                except (OSError, ValueError):
                    pass
            if schema is None:
                try:
                    schema = extract_schema(rec.path)
                except sqlite3.Error as exc:
                    raise SchemaError("The database could not be read.") from exc
                st = os.stat(rec.path)
                if (st.st_size, st.st_mtime_ns) != (rec.size, rec.mtime_ns):
                    raise SchemaError("The database changed while its schema was being read. Try again.")
                schema["sha256"] = rec.sha256
                tmp = cache.with_suffix(".tmp")
                tmp.write_text(json.dumps(schema), encoding="utf-8")
                os.replace(tmp, cache)
            sidecar = (self.schema_dir / (rec.name + ".json")) if self.schema_dir else Path("")
            out = apply_user_meta(schema, sidecar) if self.schema_dir else dict(schema, warnings=[], declared_relationships=[])
            out.update({"db_id": rec.db_id, "name": rec.name, "cached": cached})
            return out

    def prune(self):
        """Delete cached schemas whose database file no longer exists in this exact form."""
        with self._lock:
            return prune_cache(self.cache_dir, {r.sha256 for r in self.scan().records})


def prune_cache(cache_dir, live_shas):
    d = Path(cache_dir)
    if not (d / MARKER).is_file():
        raise CleanupError(f"Refusing to prune '{d}': not marked as application-owned.")
    removed = 0
    for entry in os.scandir(d):
        if entry.name == MARKER or entry.name.split(".")[0] in live_shas:
            continue
        if entry.is_file(follow_symlinks=False) or entry.is_symlink():
            os.unlink(entry.path)
            removed += 1
    return removed