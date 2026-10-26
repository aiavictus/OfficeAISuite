import io
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp.server import create_app  # noqa: E402

SUM_UNITS = {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}]}


@pytest.fixture
def env(tmp_path):
    f = fx.build_all(tmp_path / "fx")
    c = TestClient(create_app(tmp_path / "ws" / "active"))
    h = {"X-Session-Token": c.post("/api/session").json()["token"]}
    return c, h, f, tmp_path


def upload(c, h, batch, path, name=None):
    p = Path(path)
    return c.post("/api/upload", params={"batch": batch}, headers=h,
                  files={"file": (name or p.name, io.BytesIO(p.read_bytes()))})


def start(c, h):
    return c.post("/api/batch", headers=h).json()["batch"]


def fid(c, h, b, name):
    return next(x for x in c.get("/api/files", params={"batch": b}, headers=h).json()["files"] if x["name"] == name)["id"]


def test_upload_profile_suggestions_and_no_paths(env):
    c, h, f, tmp = env
    b = start(c, h)
    r = upload(c, h, b, f["orders"])
    assert r.status_code == 200 and r.json()["name"] == "orders_2025.xlsx"
    listing = c.get("/api/files", params={"batch": b}, headers=h)
    assert str(tmp) not in listing.text
    orders = listing.json()["files"][0]
    assert [t["sheet"] for t in orders["tables"]] == ["Orders", "Notes"] and orders["tables"][0]["suggestions"]


def test_session_and_batch_required(env):
    c, h, f, _ = env
    assert c.get("/api/limits").status_code == 401
    assert c.post("/api/batch", headers={"X-Session-Token": "a" * 32}).status_code == 401
    assert c.get("/api/files", params={"batch": "a" * 32}, headers=h).status_code == 409
    assert upload(c, h, "../x", f["store_b"]).status_code == 409


def test_run_every_suggestion_over_http(env):
    c, h, f, _ = env
    b = start(c, h)
    upload(c, h, b, f["orders"])
    o = c.get("/api/files", params={"batch": b}, headers=h).json()["files"][0]
    for s in o["tables"][0]["suggestions"]:
        r = c.post("/api/run", headers=h, json={"batch": b, "file_id": o["id"], "sheet": "Orders", "plan": s["plan"]})
        assert r.status_code == 200 and r.json()["columns"], s["title"]


def test_run_numbers_and_bad_plans(env):
    c, h, f, _ = env
    b = start(c, h)
    upload(c, h, b, f["store_b"])
    i = fid(c, h, b, "store_b.csv")
    ok = c.post("/api/run", headers=h, json={"batch": b, "file_id": i, "sheet": "data", "plan": SUM_UNITS}).json()
    exp = {}
    for r in fx.store_b_rows():
        exp[r[1]] = exp.get(r[1], 0) + r[3]
    assert {r[0]: r[1] for r in ok["rows"]} == exp
    for bad in ({"group_by": ["Nope"]}, {"filters": [{"column": "Region", "op": "eq", "value": "West"}]}, {"drop": 1}):
        r = c.post("/api/run", headers=h, json={"batch": b, "file_id": i, "sheet": "data", "plan": bad})
        assert r.status_code == 422 and r.json()["detail"]
    assert c.post("/api/run", headers=h, json={"batch": b, "file_id": "x" * 32, "sheet": "data", "plan": SUM_UNITS}).status_code == 409
    assert c.post("/api/run", headers=h, json={"batch": b, "file_id": i, "sheet": "Nope", "plan": SUM_UNITS}).status_code == 409


def test_new_batch_replaces_data_and_old_ids_stop_working(env):
    c, h, f, _ = env
    b1 = start(c, h)
    upload(c, h, b1, f["orders"])
    old = fid(c, h, b1, "orders_2025.xlsx")
    b2 = start(c, h)
    assert c.get("/api/files", params={"batch": b1}, headers=h).status_code == 409
    assert c.get("/api/files", params={"batch": b2}, headers=h).json()["files"] == []
    assert c.post("/api/run", headers=h, json={"batch": b2, "file_id": old, "sheet": "Orders", "plan": SUM_UNITS}).status_code == 409
    assert c.post("/api/run", headers=h, json={"batch": b1, "file_id": old, "sheet": "Orders", "plan": SUM_UNITS}).status_code == 409
    upload(c, h, b2, f["store_b"])
    assert [x["name"] for x in c.get("/api/files", params={"batch": b2}, headers=h).json()["files"]] == ["store_b.csv"]


def test_invalid_uploads_rejected_over_http(env):
    c, h, f, tmp = env
    b = start(c, h)
    for data, name in ((b"MZ\x00\x01", "evil.csv"), (b"a,b\n1,2\n", "fake.xlsx"), (b"x", "run.exe"), (b"", "e.csv"), (b"a,b\n", "h.csv")):
        r = c.post("/api/upload", params={"batch": b}, headers=h, files={"file": (name, io.BytesIO(data))})
        assert r.status_code == 400, name
    assert c.get("/api/files", params={"batch": b}, headers=h).json()["files"] == []
    assert not [p for p in (tmp / "ws").rglob("*") if p.is_file() and p.name != ".suite_owned"]


def test_upload_size_limit_over_http(env, monkeypatch):
    from dashapp import config
    c, h, f, _ = env
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 500)
    b = start(c, h)
    r = upload(c, h, b, f["orders"])
    assert r.status_code == 400 and "too large" in r.json()["detail"].lower()


def test_compare_over_http(env):
    c, h, f, _ = env
    b = start(c, h)
    upload(c, h, b, f["orders"])
    upload(c, h, b, f["store_b"])
    t = [{"file_id": fid(c, h, b, "orders_2025.xlsx"), "sheet": "Orders"}, {"file_id": fid(c, h, b, "store_b.csv"), "sheet": "data"}]
    s = c.post("/api/compare-suggestions", headers=h, json={"batch": b, "targets": t, "plan": {}}).json()
    assert s["suggestions"] and {x["column"] for x in s["shared"]} >= {"Category", "Units"}
    r = c.post("/api/compare", headers=h, json={"batch": b, "targets": t, "plan": SUM_UNITS}).json()
    assert r["columns"] == ["source", "Category", "Units_sum"] and {x[0] for x in r["rows"]} == {"orders_2025.xlsx / Orders", "store_b.csv"}
    assert c.post("/api/compare", headers=h, json={"batch": b, "targets": t[:1], "plan": SUM_UNITS}).status_code == 422
    assert c.post("/api/compare", headers=h, json={"batch": b, "targets": [t[0], t[0]], "plan": SUM_UNITS}).status_code == 400


def test_compare_limit_is_enforced(env, monkeypatch):
    from dashapp import config
    c, h, f, _ = env
    monkeypatch.setattr(config, "MAX_COMPARE", 2)
    b = start(c, h)
    for p in (f["orders"], f["store_b"], f["messy"]):
        upload(c, h, b, p)
    t = [{"file_id": fid(c, h, b, n), "sheet": s} for n, s in (("orders_2025.xlsx", "Orders"), ("store_b.csv", "data"), ("messy.csv", "data"))]
    assert c.post("/api/compare", headers=h, json={"batch": b, "targets": t, "plan": SUM_UNITS}).status_code == 422
    assert c.post("/api/compare-suggestions", headers=h, json={"batch": b, "targets": t, "plan": {}}).status_code == 400


def test_clear_removes_everything(env):
    c, h, f, tmp = env
    b = start(c, h)
    upload(c, h, b, f["orders"])
    assert c.post("/api/clear", headers=h).json() == {"cleared": True}
    assert c.get("/api/files", params={"batch": b}, headers=h).status_code == 409
    assert not [p for p in (tmp / "ws").rglob("*") if p.is_file() and p.name != ".suite_owned"]
    assert f["orders"].is_file()


def test_hardening_page_and_local_assets(env):
    c, h, _, _ = env
    assert c.get("/api/limits", headers={**h, "Host": "evil.example"}).status_code == 400
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    r = c.get("/api/limits", headers=h)
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
    html = c.get("/").text
    assert '<script src="/app.js"></script>' in html and '<script src="/plotly.min.js"></script>' in html
    assert not re.findall(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)               # no inline scripts (CSP)
    assert not re.findall(r"(?:src|href)=[\"']https?://", html)                      # nothing from the internet
    assert c.get("/app.js").status_code == 200 and len(c.get("/plotly.min.js").content) > 100_000
    assert "newSession" in c.get("/app.js").text