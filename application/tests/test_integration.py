import ast
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = {
    "project_1_documents": ("docqa", 8090, "START_PROJECT_1.bat"),
    "project_2_nl2sql": ("docsql", 8100, "START_PROJECT_2.bat"),
    "project_3_dashboard": ("dashapp", 8110, "START_PROJECT_3.bat"),
}


def test_required_files_and_launchers_exist():
    for proj, (pkg, _, bat) in PROJECTS.items():
        assert (ROOT / bat).is_file(), bat
        assert (ROOT / proj / "app" / "run.py").is_file()
        assert (ROOT / proj / "app" / pkg / "server.py").is_file()
        assert (ROOT / proj / "README.md").is_file()
    for f in ("ENV.bat", "CHECK_SUITE.bat", "VERIFY_SUITE.py", "README.md", "SETUP_STEPS.md",
              "PORTABILITY_AND_SECURITY.md", "CLEANUP_SUITE.bat"):
        assert (ROOT / f).is_file(), f


def test_launchers_use_relative_paths_only():
    for _, (_, _, bat) in PROJECTS.items():
        text = (ROOT / bat).read_text(encoding="utf-8", errors="replace")
        assert 'cd /d "%~dp0"' in text and "call ENV.bat" in text
        assert ":\\" not in text.replace("%~dp0", "")


def test_projects_do_not_import_each_other():
    names = {p[0] for p in PROJECTS.values()}
    for proj, (pkg, _, _) in PROJECTS.items():
        others = names - {pkg}
        for py in (ROOT / proj / "app").rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    assert m.split(".")[0] not in others, f"{py} imports {m}"


def test_default_ports_are_distinct_and_llm_ports_do_not_collide():
    ports = [p[1] for p in PROJECTS.values()]
    llm_ports = [p + 1 for p in ports]
    assert len(set(ports + llm_ports)) == 6


def test_model_defaults_resolve():
    sys.path.insert(0, str(ROOT / "application"))
    from shared import llm_manager
    for role in ("documents", "sql", "dashboard"):
        assert llm_manager.default_model_id(role)


def _free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("proj", list(PROJECTS))
def test_each_app_starts_binds_locally_and_stops(proj):
    pkg, port, _ = PROJECTS[proj]
    test_port = port + 100
    assert _free(test_port)
    proc = subprocess.Popen([sys.executable, str(ROOT / proj / "app" / "run.py"), "--port", str(test_port)],
                            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ok = False
        for _ in range(40):
            if proc.poll() is not None:
                break
            try:
                r = httpx.get(f"http://127.0.0.1:{test_port}/", timeout=2, trust_env=False)
                ok = r.status_code in (200, 404)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        assert ok, f"{proj} did not answer on 127.0.0.1:{test_port}"
        assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    time.sleep(0.5)
    assert _free(test_port)