import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_sample_db  # noqa: E402
from docsql import catalog as cat, config  # noqa: E402
from docsql.catalog import DatabaseCatalog, SchemaError  # noqa: E402
from shared import cleanup  # noqa: E402

SHOP = "db:shop.sqlite"


def stamp(p, t):
    os.utime(p, ns=(t * 10**9, t * 10**9))


@pytest.fixture
def env(tmp_path):
    d = tmp_path / "dbs"
    d.mkdir()
    return d, DatabaseCatalog(d, tmp_path / "cache", tmp_path / "schemas"), tmp_path


def put_shop(d, name="shop.sqlite", t=1000):
    p = make_sample_db.build(d / name)
    stamp(p, t)
    return p


def names(records):
    return [r.name for r in records]


# ---- discovery ----
def test_discovery_by_content_not_extension(env):
    d, c, _ = env
    put_shop(d)
    (d / "notes.txt").write_text("hello")
    (d / "fake.db").write_bytes(b"this is not sqlite")
    (d / ".hidden.db").write_bytes(b"x")
    (d / "sub").mkdir()
    r = c.scan()
    assert names(r.records) == ["shop.sqlite"] and names(r.new) == ["shop.sqlite"]
    assert [n for n, _ in r.invalid] == ["fake.db"]


def test_header_only_corrupt_file_is_invalid(env):
    d, c, _ = env
    (d / "bad.sqlite").write_bytes(cat.SQLITE_MAGIC + b"\xff" * 200)
    r = c.scan()
    assert r.records == [] and [n for n, _ in r.invalid] == ["bad.sqlite"]


def test_oversize_file_rejected(env, monkeypatch):
    d, c, _ = env
    put_shop(d)
    monkeypatch.setattr(config, "MAX_DB_BYTES", 100)
    r = c.scan()
    assert r.records == [] and r.invalid[0][0] == "shop.sqlite"


def test_unchanged_files_not_rehashed(env, monkeypatch):
    d, c, _ = env
    put_shop(d)
    c.scan()
    calls = []
    real = cat._sha256
    monkeypatch.setattr(cat, "_sha256", lambda p: calls.append(p) or real(p))
    r = c.scan()
    assert calls == [] and names(r.unchanged) == ["shop.sqlite"] and not r.new and not r.modified


# ---- change detection: never a stale schema ----
def test_modified_database_gives_new_schema(env):
    d, c, _ = env
    p = put_shop(d)
    s1 = c.get_schema(SHOP)
    with closing(sqlite3.connect(p)) as con:
        con.execute("ALTER TABLE customers ADD COLUMN email TEXT")
        con.commit()
    stamp(p, 2000)
    assert names(c.scan().modified) == ["shop.sqlite"]
    s2 = c.get_schema(SHOP)
    cols = lambda s: [x["name"] for t in s["tables"] if t["name"] == "customers" for x in t["columns"]]
    assert "email" not in cols(s1) and "email" in cols(s2) and s1["sha256"] != s2["sha256"]


def test_replaced_database_detected(env):
    d, c, _ = env
    p = put_shop(d)
    c.scan()
    p.unlink()
    with closing(sqlite3.connect(p)) as con:
        con.execute("CREATE TABLE only_one (x INTEGER)")
        con.commit()
    stamp(p, 3000)
    assert names(c.scan().modified) == ["shop.sqlite"]
    assert [t["name"] for t in c.get_schema(SHOP)["tables"]] == ["only_one"]


def test_removed_database_cannot_be_used(env):
    d, c, _ = env
    p = put_shop(d)
    c.get_schema(SHOP)
    p.unlink()
    with pytest.raises(SchemaError):
        c.get_schema(SHOP)


def test_removed_is_reported_once(env):
    d, c, _ = env
    p = put_shop(d)
    c.scan()
    p.unlink()
    r = c.scan()
    assert r.removed == ["shop.sqlite"] and r.records == []


@pytest.mark.parametrize("bad", ["db:../x", "shop.sqlite", None, 5, "db:ghost.sqlite", "db:..\\shop.sqlite"])
def test_bad_ids_rejected(env, bad):
    d, c, _ = env
    put_shop(d)
    with pytest.raises(SchemaError):
        c.get_schema(bad)


# ---- schema content ----
def test_schema_content(env):
    d, c, _ = env
    put_shop(d)
    s = c.get_schema(SHOP)
    t = {x["name"]: x for x in s["tables"]}
    assert sorted(t) == ["customer_orders", "customers", "order_items", "orders", "products"]
    assert t["customer_orders"]["kind"] == "view" and t["customers"]["kind"] == "table"
    cust = {x["name"]: x for x in t["customers"]["columns"]}
    assert cust["name"]["type"] == "TEXT" and cust["name"]["notnull"] is True and cust["id"]["pk"] == 1
    assert {x["name"]: x["type"] for x in t["products"]["columns"]}["price"] == "REAL"
    assert t["order_items"]["primary_key"] == ["order_id", "line_no"]
    assert t["orders"]["foreign_keys"] == [{"from": ["customer_id"], "table": "customers", "to": ["id"]}]
    fks = sorted(t["order_items"]["foreign_keys"], key=lambda f: f["from"])
    assert fks == [{"from": ["order_id"], "table": "orders", "to": ["id"]},
                   {"from": ["product_id"], "table": "products", "to": ["id"]}]
    assert s["dialect"] == "sqlite" and s["name"] == "shop.sqlite"


def test_schema_cached_by_sha(env, monkeypatch):
    d, c, tmp = env
    put_shop(d)
    calls = []
    real = cat.extract_schema
    monkeypatch.setattr(cat, "extract_schema", lambda p: calls.append(p) or real(p))
    s1 = c.get_schema(SHOP)
    s2 = c.get_schema(SHOP)
    assert len(calls) == 1 and s1["cached"] is False and s2["cached"] is True
    assert (tmp / "cache" / f"{s1['sha256']}.json").is_file()


# ---- read-only ----
def test_connection_is_read_only(env):
    d, c, _ = env
    p = put_shop(d)
    before = cat._sha256(p)
    with closing(cat.open_readonly(p)) as con:
        assert con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 4
        for stmt in ("INSERT INTO customers(name) VALUES ('x')", "CREATE TABLE t (a)", "DROP TABLE customers"):
            with pytest.raises(sqlite3.OperationalError):
                con.execute(stmt)
    assert cat._sha256(p) == before


# ---- optional description file ----
def test_sidecar_merged_and_validated(env):
    d, c, tmp = env
    put_shop(d)
    (tmp / "schemas").mkdir()
    (tmp / "schemas" / "shop.sqlite.json").write_text(json.dumps({
        "tables": {"customers": {"description": "People who buy", "columns": {"city": "Home city", "zzz": "x"}},
                   "ghost": {"description": "nope"}},
        "relationships": [{"from": "orders.customer_id", "to": "customers.id"},
                          {"from": "orders.nope", "to": "customers.id"}]}), encoding="utf-8")
    s = c.get_schema(SHOP)
    t = {x["name"]: x for x in s["tables"]}
    assert t["customers"]["description"] == "People who buy"
    assert {x["name"]: x for x in t["customers"]["columns"]}["city"]["description"] == "Home city"
    assert s["declared_relationships"] == [{"from": "orders.customer_id", "to": "customers.id"}]
    assert len(s["warnings"]) == 3


def test_broken_sidecar_is_ignored_not_fatal(env):
    d, c, tmp = env
    put_shop(d)
    (tmp / "schemas").mkdir()
    (tmp / "schemas" / "shop.sqlite.json").write_text("{not json", encoding="utf-8")
    s = c.get_schema(SHOP)
    assert len(s["tables"]) == 5 and s["warnings"] and s["declared_relationships"] == []


# ---- cache pruning ----
def test_prune_removes_only_stale_entries(env):
    d, c, tmp = env
    p = put_shop(d)
    old = c.get_schema(SHOP)["sha256"]
    with closing(sqlite3.connect(p)) as con:
        con.execute("ALTER TABLE products ADD COLUMN sku TEXT")
        con.commit()
    stamp(p, 2000)
    new = c.get_schema(SHOP)["sha256"]
    assert c.prune() == 1
    assert not (tmp / "cache" / f"{old}.json").exists() and (tmp / "cache" / f"{new}.json").exists()


def test_prune_refuses_unmarked_folder(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "x.json").write_text("{}")
    with pytest.raises(cleanup.CleanupError):
        cat.prune_cache(d, set())
    assert (d / "x.json").exists()