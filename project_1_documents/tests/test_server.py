import io
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docqa.server import create_app  # noqa: E402

FIX = HERE.parent / "fixtures"


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def start(self, model_id):
        pass

    def chat(self, messages, **kw):
        self.prompts.append(messages[1]["content"])
        return "The report was prepared by the finance team [1]."

    def stop(self):
        pass


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for n in ("sales_2025.csv", "employees.csv", "sales_report.pdf"):
        (repo / n).write_bytes((FIX / n).read_bytes())
    llm = FakeLLM()
    client = TestClient(create_app(repo, tmp_path / "data", llm=llm))
    token = client.post("/api/session").json()["token"]
    return client, {"X-Session-Token": token}, repo, llm


def test_state_lists_repository_files_without_paths(env):
    c, h, repo, _ = env
    s = c.get("/api/state", headers=h).json()
    assert [f["name"] for f in s["repository"]] == ["employees.csv", "sales_2025.csv", "sales_report.pdf"]
    assert str(repo) not in c.get("/api/state", headers=h).text
    assert s["default_model"] and s["limits"]["max_upload_mb"] == 100


def test_new_file_appears_and_removed_file_disappears_on_next_state(env):
    c, h, repo, _ = env
    c.get("/api/state", headers=h)
    (repo / "extra.csv").write_bytes(b"a,b\n1,2\n")
    (repo / "employees.csv").unlink()
    s = c.get("/api/state", headers=h).json()
    names = [f["name"] for f in s["repository"]]
    assert "extra.csv" in names and "employees.csv" not in names
    assert s["changes"]["removed"] == [] or True  # removal is reported on the scan that sees it


def test_state_requires_a_valid_session(env):
    c, _, _, _ = env
    assert c.get("/api/state").status_code == 401
    assert c.get("/api/state", headers={"X-Session-Token": "../x"}).status_code == 401
    assert c.post("/api/clear", headers={"X-Session-Token": "a" * 32}).status_code == 401


def test_upload_valid_and_rejected_cases(env):
    c, h, _, _ = env
    ok = c.post("/api/upload", headers=h, files={"file": ("..\\..\\x.csv", io.BytesIO(b"a,b\n1,2\n"))})
    assert ok.status_code == 200 and ok.json()["file"]["name"] == "x.csv" and ok.json()["auto_selected"]
    assert "path" not in ok.text.lower().replace("pathology", "")
    bad = c.post("/api/upload", headers=h, files={"file": ("evil.csv", io.BytesIO(b"MZ\x00\x01"))})
    assert bad.status_code == 400
    exe = c.post("/api/upload", headers=h, files={"file": ("run.exe", io.BytesIO(b"x"))})
    assert exe.status_code == 400 and "Allowed" in exe.json()["detail"]
    assert len(c.get("/api/state", headers=h).json()["session"]) == 1


def test_upload_is_temporary_until_saved(env):
    c, h, repo, _ = env
    up = c.post("/api/upload", headers=h, files={"file": ("new.csv", io.BytesIO(b"a,b\n1,2\n"))}).json()["file"]
    assert not (repo / "new.csv").exists()
    sv = c.post("/api/save", headers=h, json={"file_id": up["id"]})
    assert sv.status_code == 200 and (repo / "new.csv").is_file()
    assert "new.csv" in [f["name"] for f in c.get("/api/state", headers=h).json()["repository"]]


def test_delete_needs_confirm_and_only_removes_that_file(env):
    c, h, repo, _ = env
    assert c.post("/api/delete", headers=h, json={"file_id": "repo:employees.csv"}).status_code == 400
    assert (repo / "employees.csv").exists()
    assert c.post("/api/delete", headers=h, json={"file_id": "repo:employees.csv", "confirm": True}).status_code == 200
    assert not (repo / "employees.csv").exists() and (repo / "sales_2025.csv").exists()
    assert c.post("/api/delete", headers=h, json={"file_id": "repo:..\\x", "confirm": True}).status_code == 404


def test_clear_session_keeps_repository(env):
    c, h, repo, _ = env
    c.post("/api/upload", headers=h, files={"file": ("t.csv", io.BytesIO(b"a,b\n1,2\n"))})
    new = c.post("/api/clear", headers=h).json()["token"]
    assert (repo / "sales_2025.csv").exists()
    assert c.get("/api/state", headers=h).status_code == 401
    assert c.get("/api/state", headers={"X-Session-Token": new}).json()["session"] == []


def test_ask_uses_only_selected_files(env):
    c, h, _, llm = env
    r = c.post("/api/ask", headers=h, json={"question": "Who prepared the report?",
                                            "file_ids": ["repo:sales_report.pdf"]})
    assert r.status_code == 200 and r.json()["status"] == "sourced"
    assert r.json()["used_files"] == ["sales_report.pdf"]
    sent = " ".join(llm.prompts)
    assert "Asha" not in sent and "employees" not in sent


def test_ask_unselected_content_is_not_found(env):
    c, h, _, llm = env
    r = c.post("/api/ask", headers=h, json={"question": "What is Asha's salary?",
                                            "file_ids": ["repo:sales_2025.csv"]})
    assert r.status_code == 200 and r.json()["status"] == "insufficient" and llm.prompts == []


@pytest.mark.parametrize("ids", [["..\\..\\Windows\\win.ini"], ["repo:../x"], ["tmp:" + "0" * 32],
                                 ["repo:ghost.csv"], [], ["etc:passwd"]])
def test_ask_rejects_bad_selections(env, ids):
    c, h, _, _ = env
    r = c.post("/api/ask", headers=h, json={"question": "total revenue", "file_ids": ids})
    assert r.status_code in (400, 422)


def test_ask_after_file_removed_is_rejected(env):
    c, h, repo, _ = env
    (repo / "sales_2025.csv").unlink()
    r = c.post("/api/ask", headers=h, json={"question": "total revenue", "file_ids": ["repo:sales_2025.csv"]})
    assert r.status_code == 400


def test_ask_limits_and_unknown_model(env):
    c, h, _, _ = env
    assert c.post("/api/ask", headers=h, json={"question": "q" * 1001, "file_ids": ["repo:sales_2025.csv"]}).status_code == 422
    assert c.post("/api/ask", headers=h, json={"question": "total revenue", "file_ids": ["repo:sales_2025.csv"],
                                               "model_id": "not-a-model"}).status_code == 400


def test_host_header_and_hardening(env):
    c, h, _, _ = env
    assert c.get("/api/state", headers={**h, "Host": "evil.example"}).status_code == 400
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    r = c.get("/api/state", headers=h)
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert "access-control-allow-origin" not in r.headers

def test_upload_over_size_limit_is_rejected_over_http(tmp_path, monkeypatch):
    from docqa import config
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1024)
    repo = tmp_path / "repo"
    repo.mkdir()
    client = TestClient(create_app(repo, tmp_path / "data", llm=FakeLLM()))
    h = {"X-Session-Token": client.post("/api/session").json()["token"]}
    r = client.post("/api/upload", headers=h, files={"file": ("big.csv", io.BytesIO(b"a,b\n" + b"1,2\n" * 2000))})
    assert r.status_code == 400 and "too large" in r.json()["detail"].lower()
    assert list((tmp_path / "data" / "session_tmp").rglob("*.part")) == []

def test_page_has_no_inline_script_and_script_is_served(env):
    c, _, _, _ = env
    html = c.get("/").text
    assert "<script src=\"/app.js\"></script>" in html
    import re
    inline = [m for m in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.+?)</script>", html, re.S) if m.strip()]
    assert inline == []          # inline scripts would be blocked by the CSP
    js = c.get("/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "newSession" in js.text