"""Read-only execution of VALIDATED SQL with defence in depth:
mode=ro connection, query_only, authorizer (known tables, allowlisted functions only),
progress-handler deadline, SQLite length/depth limits, row cap, result-size cap,
and a check that the database file is still the one the SQL was validated against.
"""
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field

from .catalog import open_readonly
from .validate import SAFE_FUNCTIONS

MAX_CELL_CHARS = 1000


class QueryError(RuntimeError):
    """Execution failed. The message is safe to show to the user."""


@dataclass
class QueryResult:
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: int = 0


def _authorizer(table_names):
    names = {t.lower() for t in table_names}

    def cb(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 and arg1.lower() in names else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if arg2 and arg2.lower() in SAFE_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    return cb


def _cell(v):
    if isinstance(v, bytes):
        return f"<binary {len(v)} bytes>"
    if isinstance(v, str) and len(v) > MAX_CELL_CHARS:
        return v[:MAX_CELL_CHARS] + "..."
    return v


def _execute(path, sql, table_names, max_rows=500, max_bytes=1_000_000, timeout_s=5.0):
    """table_names must list every real table the schema contains (views read their base tables)."""
    """Run SQL under every control. Callers outside tests should use run_query."""
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    try:
        with closing(open_readonly(path)) as conn:
            if hasattr(conn, "setlimit"):
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
                conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 10_000)
                conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 50)
                conn.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 10)
                conn.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 200)
            conn.set_authorizer(_authorizer(table_names))
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
            cur = conn.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows, size, truncated = [], 0, False
            while True:
                batch = cur.fetchmany(100)
                if not batch:
                    break
                for r in batch:
                    cells = [_cell(v) for v in r]
                    size += sum(len(str(v)) for v in cells)
                    if len(rows) >= max_rows or size > max_bytes:
                        truncated = True
                        break
                    rows.append(cells)
                if truncated:
                    break
    except sqlite3.Error as exc:
        if time.monotonic() > deadline:
            raise QueryError(f"The query timed out after {timeout_s:g} seconds.") from exc
        raise QueryError(f"The query could not be run: {exc}") from exc
    return QueryResult(cols, rows, truncated, int((time.monotonic() - t0) * 1000))


def run_query(record, checked, **limits):
    """Execute a Checked query against the CURRENT database file. Raises QueryError if it changed."""
    try:
        st = os.stat(record.path)
    except OSError as exc:
        raise QueryError("The database is no longer available.") from exc
    if (st.st_size, st.st_mtime_ns) != (record.size, record.mtime_ns) or checked.schema_sha != record.sha256:
        raise QueryError("The database changed since the SQL was generated. Generate it again.")
    return _execute(record.path, checked.sql, checked.all_tables or checked.tables, **limits)