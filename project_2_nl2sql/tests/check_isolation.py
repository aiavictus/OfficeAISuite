"""Proves answers come only from the SELECTED database. Read-only; uses a fake model, no LLM needed.
    python project_2_nl2sql\\tests\\check_isolation.py
"""
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docsql import config  # noqa: E402
from docsql.server import create_app  # noqa: E402


class Fake:
    reply = "SELECT 1"

    def start(self, m): pass
    def chat(self, messages, **kw): return Fake.reply
    def complete(self, prompt, **kw): return Fake.reply


def main():
    ok = True
    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(("[PASS] " if cond else "[FAIL] ") + name + (f"  {detail}" if not cond else ""))

    c = TestClient(create_app(config.DB_DIR, Path(tempfile.mkdtemp()), config.SCHEMA_DIR, llm=Fake()))
    h = {"X-Session-Token": c.post("/api/session").json()["token"]}
    dbs = c.get("/api/state", headers=h).json()["databases"]
    print("Databases:", [d["name"] for d in dbs])
    tables = {}
    for d in dbs:
        s = c.get("/api/schema", params={"db_id": d["id"]}, headers=h).json()
        tables[d["id"]] = {t["name"] for t in s["tables"]}
    for a in dbs:                                         # a table that exists only in ANOTHER database must be refused
        for b in dbs:
            if a["id"] == b["id"]:
                continue
            for t in sorted(tables[b["id"]] - tables[a["id"]]):
                Fake.reply = f"SELECT * FROM {t}"
                r = c.post("/api/preview", json={"db_id": a["id"], "question": "x"}, headers=h).json()
                check(f"{t} (only in {b['name']}) refused on {a['name']}", r["status"] == "rejected" and not r["executed"])
    for d in dbs:                                         # rows must equal what the selected FILE itself contains
        for t in sorted(tables[d["id"]]):
            Fake.reply = f"SELECT COUNT(*) FROM {t}"
            r = c.post("/api/preview", json={"db_id": d["id"], "question": "x"}, headers=h).json()
            with closing(sqlite3.connect(f"file:{(config.DB_DIR / d['name']).as_posix()}?mode=ro", uri=True)) as con:
                direct = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            check(f"{d['name']}.{t}: API count == direct file count ({direct})", r.get("rows") == [[direct]],
                  f"api={r.get('rows')} file={direct}")
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())