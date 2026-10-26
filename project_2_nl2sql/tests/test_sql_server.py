import io
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_sample_db  # noqa: E402
from docsql.server import create_app  # noqa: E402


class Fake:
    def __init__(self, reply="SELECT COUNT(*) FROM customers"):
        self.reply, self.calls = reply, 0

    def start(self, mid):
        pass

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply

    def complete(self, prompt, **kw):
        self.calls += 1
        return self.reply


def make(tmp_path, reply="SELECT COUNT(*) FROM customers"):
    dbs = tmp_path / "dbs"
    dbs.mkdir(exist_ok=True)
    make_sample_db.build(dbs / "shop.sqlite")
    llm = Fake(reply)
    c = TestClient(create_app(dbs, tmp_path / "data", tmp_path / "schemas", llm=llm))
    h = {"X-Session-Token": c.post("/api/session").json()["token"]}
    return c, h, dbs, llm


ASK = {"db_id": "db:shop.sqlite", "question": "How many customers?"}


def test_state_schema_and_no_paths(tmp_path):
    c, h, dbs, _ = make(tmp_path)
    s = c.get("/api/state", headers=h)
    assert [d["name"] for d in s.json()["databases"]] == ["shop.sqlite"] and str(dbs) not in s.text
    sch = c.get("/api/schema", params={"db_id": "db:shop.sqlite"}, headers=h).json()
    assert "customers" in [t["name"] for t in sch["tables"]]


def test_session_required(tmp_path):
    c, _, _, _ = make(tmp_path)
    assert c.get("/api/state").status_code == 401
    assert c.post("/api/generate", json=ASK, headers={"X-Session-Token": "a" * 32}).status_code == 401


def test_generate_only_never_executes(tmp_path):
    c, h, _, _ = make(tmp_path)
    r = c.post("/api/generate", json=ASK, headers=h).json()
    assert r["status"] == "ok" and r["executed"] is False and "rows" not in r and r["sql"].upper().startswith("SELECT")


def test_preview_executes_read_only(tmp_path):
    c, h, _, _ = make(tmp_path)
    r = c.post("/api/preview", json=ASK, headers=h).json()
    assert r["executed"] is True and r["columns"] and r["rows"] == [[4]]


@pytest.mark.parametrize("reply", ["DROP TABLE customers", "SELECT * FROM secrets", "SELECT 1; DELETE FROM customers"])
def test_unsafe_model_output_is_a_generation_failure_and_changes_nothing(tmp_path, reply):
    c, h, dbs, _ = make(tmp_path, reply)
    before = (dbs / "shop.sqlite").read_bytes()
    r = c.post("/api/preview", json=ASK, headers=h).json()
    assert r["status"] == "rejected" and r["stage"] == "generation" and r["executed"] is False and r["reason"]
    assert (dbs / "shop.sqlite").read_bytes() == before


def test_execution_failure_is_reported_separately(tmp_path, monkeypatch):
    from docsql import executor
    c, h, _, _ = make(tmp_path)
    monkeypatch.setattr(executor, "run_query", lambda *a, **k: (_ for _ in ()).throw(executor.QueryError("boom")))
    r = c.post("/api/preview", json=ASK, headers=h).json()
    assert r["status"] == "execution_failed" and r["stage"] == "execution" and r["sql"]


def test_changed_database_uses_the_new_schema(tmp_path):
    c, h, dbs, _ = make(tmp_path, "SELECT email FROM customers")
    assert c.post("/api/generate", json=ASK, headers=h).json()["status"] == "rejected"      # no email column yet
    p = dbs / "shop.sqlite"
    with closing(sqlite3.connect(p)) as con:
        con.execute("ALTER TABLE customers ADD COLUMN email TEXT")
        con.commit()
    os.utime(p, ns=(5000 * 10**9, 5000 * 10**9))
    assert c.post("/api/generate", json=ASK, headers=h).json()["status"] == "ok"


def test_removed_or_bad_database_ids(tmp_path):
    c, h, dbs, _ = make(tmp_path)
    for bad in ("db:../x", "shop.sqlite", "db:ghost.sqlite", "db:..\\shop.sqlite"):
        assert c.post("/api/generate", json={**ASK, "db_id": bad}, headers=h).status_code == 404
    (dbs / "shop.sqlite").unlink()
    assert c.post("/api/preview", json=ASK, headers=h).status_code == 404


def test_limits_and_unknown_model(tmp_path):
    c, h, _, _ = make(tmp_path)
    assert c.post("/api/generate", json={**ASK, "question": "q" * 501}, headers=h).status_code == 422
    assert c.post("/api/generate", json={**ASK, "model_id": "nope"}, headers=h).status_code == 400
    assert c.post("/api/generate", json={**ASK, "model_id": "sqlcoder-7b"}, headers=h).status_code == 400


def test_upload_database_validated_by_content(tmp_path):
    c, h, dbs, _ = make(tmp_path)
    src = (dbs / "shop.sqlite").read_bytes()
    ok = c.post("/api/upload-db", headers=h, files={"file": ("..\\..\\new.sqlite", io.BytesIO(src))})
    assert ok.status_code == 200 and (dbs / "new.sqlite").is_file()
    again = c.post("/api/upload-db", headers=h, files={"file": ("new.sqlite", io.BytesIO(src))}).json()
    assert again["saved_as"] == "new (2).sqlite"
    assert c.post("/api/upload-db", headers=h, files={"file": ("fake.sqlite", io.BytesIO(b"not a db"))}).status_code == 400
    assert c.post("/api/upload-db", headers=h, files={"file": ("x.exe", io.BytesIO(src))}).status_code == 400
    assert not (dbs / "fake.sqlite").exists()
    assert list((tmp_path / "data" / "upload_tmp").glob("*.part")) == []


def test_upload_size_limit(tmp_path, monkeypatch):
    from docsql import config
    c, h, dbs, _ = make(tmp_path)
    monkeypatch.setattr(config, "MAX_DB_BYTES", 1000)
    r = c.post("/api/upload-db", headers=h, files={"file": ("big.sqlite", io.BytesIO(b"x" * 5000))})
    assert r.status_code == 400 and "too large" in r.json()["detail"].lower()


def test_hardening_and_page(tmp_path):
    c, h, _, _ = make(tmp_path)
    assert c.get("/api/state", headers={**h, "Host": "evil.example"}).status_code == 400
    assert c.get("/docs").status_code == 404
    r = c.get("/api/state", headers=h)
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    html = c.get("/").text
    assert '<script src="/app.js"></script>' in html and "newSession" in c.get("/app.js").text

def test_description_shown_in_state(tmp_path):
    c, h, _, _ = make(tmp_path)
    (tmp_path / "schemas").mkdir(exist_ok=True)
    (tmp_path / "schemas" / "shop.sqlite.json").write_text('{"description": "Sample shop"}', encoding="utf-8")
    assert c.get("/api/state", headers=h).json()["databases"][0]["description"] == "Sample shop"


def test_each_database_uses_only_its_own_schema(tmp_path):
    c, h, dbs, _ = make(tmp_path, "SELECT * FROM only_here")
    with closing(sqlite3.connect(dbs / "other.sqlite")) as con:
        con.execute("CREATE TABLE only_here (x INTEGER)")
        con.commit()
    assert c.post("/api/generate", json={**ASK, "db_id": "db:shop.sqlite"}, headers=h).json()["status"] == "rejected"
    assert c.post("/api/generate", json={**ASK, "db_id": "db:other.sqlite"}, headers=h).json()["status"] == "ok"

def test_run_edited_sql_is_validated_and_read_only(tmp_path):
    c, h, dbs, _ = make(tmp_path)
    run = lambda sql, hdr=h, db="db:shop.sqlite": c.post("/api/run-sql", json={"db_id": db, "sql": sql}, headers=hdr)  # noqa: E731
    ok = run("SELECT COUNT(*) FROM customers").json()
    assert ok["status"] == "ok" and ok["rows"] == [[4]] and ok["executed"] is True
    before = (dbs / "shop.sqlite").read_bytes()
    for bad in ("DROP TABLE customers", "SELECT 1; DELETE FROM customers", "SELECT * FROM secrets",
                "SELECT load_extension('x')", "SELECT * FROM customers -- c"):
        r = run(bad).json()
        assert r["status"] == "rejected" and r["executed"] is False and r["reason"], bad
    assert (dbs / "shop.sqlite").read_bytes() == before
    assert run("SELECT 1", {"X-Session-Token": "a" * 32}).status_code == 401
    assert run("SELECT 1", db="db:ghost.sqlite").status_code == 404
    assert run("x" * 4001).status_code == 422