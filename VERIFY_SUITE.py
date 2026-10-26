"""Suite verification. Run through CHECK_SUITE.bat (it activates the bundled Python).

  CHECK_SUITE.bat            full check (includes the pip offline dry-run)
  CHECK_SUITE.bat --quick    skip the pip dry-run and the subprocess version calls
  CHECK_SUITE.bat --full     also re-hash EVERY manifest file, including models (slow)
  CHECK_SUITE.bat --load     also start each default model and ask it a question

Exit code 0 only when there are no FAIL results. WARN means "review", not "broken".
This does NOT prove the suite works offline: that needs a manual test with networking disabled.
"""
import argparse
import importlib
import importlib.metadata as md
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "application"
sys.path.insert(0, str(APP / "scripts"))
sys.path.insert(0, str(APP))

STATUSES = []
REQUIRED_DIRS = [
    "application/python", "application/site-packages", "application/wheelhouse",
    "application/llm_runtime/llama.cpp", "application/models", "application/ocr/tesseract",
    "application/shared", "application/scripts", "application/tests",
    "project_1_documents", "project_2_nl2sql", "project_3_dashboard",
]
IMPORTS = ["fastapi", "uvicorn", "pandas", "numpy", "openpyxl", "pdfplumber", "pypdf", "pypdfium2",
           "PIL", "pytesseract", "sqlglot", "rank_bm25", "httpx", "pydantic", "plotly", "pytest"]
PROFILE_KEYS = {"port", "ctx", "threads", "threads_batch", "batch", "gpu_layers"}
ROLES = ("documents", "sql", "dashboard")
SCAN_SKIP = {"python", "site-packages", "wheelhouse", "llm_runtime", "models", "ocr",
             "_downloads", "logs", "data", "__pycache__"}
SCAN_SUFFIXES = {".py", ".bat", ".json", ".md", ".txt", ".ini"}
USER_PATH = re.compile("[A-Za-z]:" + r"[\\/]" + "Users" + r"[\\/]")  # machine-specific user paths


def rec(status, name, detail=""):
    STATUSES.append(status)
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def norm(name):
    return re.sub(r"[-_.]+", "_", name).lower()


def inside(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def read_pins(path):
    pins = {}
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;#]+)", line.strip())
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- checks
def check_structure():
    missing = [d for d in REQUIRED_DIRS if not (ROOT / d).is_dir()]
    rec("FAIL" if missing else "PASS", "folder structure", ", ".join(missing))
    for f in ("ENV.bat", "VERIFY_SUITE.py", "CHECK_SUITE.bat", "README.md", "SETUP_STEPS.md",
              "PORTABILITY_AND_SECURITY.md", "application/models/MODEL_INVENTORY.md",
              "application/LICENSES/THIRD_PARTY.md"):
        if not (ROOT / f).is_file():
            rec("FAIL", f"root file {f}", "missing")


def check_python():
    v = sys.version_info
    rec("PASS" if (v.major, v.minor) == (3, 11) else "FAIL", "python version", f"{v.major}.{v.minor}.{v.micro}")
    bundled = inside(sys.executable, APP / "python")
    rec("PASS" if bundled else "FAIL", "running the bundled python",
        sys.executable if not bundled else "")
    pth = APP / "python" / "python311._pth"
    ok = pth.is_file() and "..\\site-packages" in pth.read_text(errors="replace")
    rec("PASS" if ok else "FAIL", "python311._pth points to ..\\site-packages")
    external = [p for p in sys.path if "site-packages" in p.lower() and not inside(p, APP)]
    rec("WARN" if external else "PASS", "no external site-packages on sys.path", "; ".join(external))


def check_packages():
    req, lock = APP / "requirements.in", APP / "requirements.lock.txt"
    if not req.is_file() or not lock.is_file():
        rec("FAIL", "requirements files", "requirements.in or requirements.lock.txt missing")
        return
    bad = []
    for name, ver in read_pins(req).items():
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            bad.append(f"{name} not installed")
            continue
        if have != ver:
            bad.append(f"{name} {have} != pinned {ver}")
    rec("FAIL" if bad else "PASS", "pinned packages installed", "; ".join(bad[:8]))
    failed = []
    for mod in IMPORTS:
        try:
            m = importlib.import_module(mod)
            f = getattr(m, "__file__", None)
            if f and not inside(f, APP):
                failed.append(f"{mod} loaded from outside the suite")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{mod}: {exc.__class__.__name__}")
    rec("FAIL" if failed else "PASS", "imports", "; ".join(failed))


def check_wheelhouse(quick):
    wh = APP / "wheelhouse"
    wheels = list(wh.glob("*.whl"))
    if not wheels:
        rec("FAIL", "wheelhouse", "no wheels found")
        return
    index, bad_platform = set(), []
    for w in wheels:
        parts = w.name[:-4].split("-")
        index.add((norm(parts[0]), parts[1]))
        plat = parts[-1].split(".")
        if not any(t == "any" or t.startswith("win_amd64") for t in plat):
            bad_platform.append(w.name)
    rec("FAIL" if bad_platform else "PASS", f"wheelhouse platform tags ({len(wheels)} wheels)",
        "; ".join(bad_platform[:5]))
    lock = read_pins(APP / "requirements.lock.txt")
    missing, soft = [], []
    for name, ver in lock.items():
        if (norm(name), ver) not in index:
            (soft if norm(name) in {"pip", "setuptools", "wheel"} else missing).append(f"{name}=={ver}")
    rec("FAIL" if missing else "PASS", "every locked package has a wheel", "; ".join(missing[:8]))
    if soft:
        rec("WARN", "tooling wheels not in wheelhouse", "; ".join(soft))
    if quick:
        rec("WARN", "pip offline dry-run", "skipped (--quick)")
        return
    cmd = [sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(wh),
           "--dry-run", "--ignore-installed", "--only-binary=:all:",
           "-r", str(APP / "requirements.in"), "--no-warn-script-location"]
    try:
        r = run(cmd, 300)
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        rec("PASS" if r.returncode == 0 else "FAIL", "pip offline dry-run resolves from wheelhouse only",
            "" if r.returncode == 0 else " | ".join(tail))
    except Exception as exc:  # noqa: BLE001
        rec("FAIL", "pip offline dry-run", str(exc))


def check_runtime(quick):
    d = APP / "llm_runtime" / "llama.cpp"
    exe = d / "llama-server.exe"
    if not exe.is_file():
        rec("FAIL", "llama-server.exe", "missing")
        return
    rec("PASS", "llama-server.exe present")
    rec("PASS" if (d / "RUNTIME_VERSION.txt").is_file() else "WARN", "RUNTIME_VERSION.txt")
    dlls = [x for x in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll") if not (d / x).is_file()]
    rec("WARN" if dlls else "PASS", "app-local VC++ runtime DLLs (llama.cpp)", ", ".join(dlls))
    if quick:
        return
    try:
        r = run([str(exe), "--version"], 60)
        out = (r.stdout + r.stderr).strip().splitlines()
        rec("PASS" if r.returncode == 0 else "FAIL", "llama-server --version", out[0] if out else "")
    except Exception as exc:  # noqa: BLE001
        rec("FAIL", "llama-server --version", str(exc))


def check_ocr(quick):
    d = APP / "ocr" / "tesseract"
    exe = d / "tesseract.exe"
    rec("PASS" if exe.is_file() else "FAIL", "tesseract.exe present")
    rec("PASS" if (d / "tessdata" / "eng.traineddata").is_file() else "FAIL", "tessdata\\eng.traineddata")
    if exe.is_file() and not quick:
        try:
            r = run([str(exe), "--version"], 60)
            out = (r.stdout + r.stderr).strip().splitlines()
            rec("PASS" if r.returncode == 0 else "FAIL", "tesseract --version", out[0] if out else "")
        except Exception as exc:  # noqa: BLE001
            rec("FAIL", "tesseract --version", str(exc))


def check_models(load):
    try:
        from shared import llm_manager as lm
        models = lm.load_models()
        profiles = json.loads((APP / "shared" / "config" / "llm_profiles.json").read_text(encoding="utf-8"))["profiles"]
    except Exception as exc:  # noqa: BLE001
        rec("FAIL", "models.json / llm_profiles.json", f"{exc.__class__.__name__}: {exc}")
        return
    bad_prof = [n for n, p in profiles.items() if not PROFILE_KEYS <= set(p)]
    rec("FAIL" if bad_prof else "PASS", "llm profiles complete", ", ".join(bad_prof))
    referenced = set()
    for m in models:
        p = APP / "models" / m["file"]
        referenced.add(p.name.lower())
        if not p.is_file():
            rec("FAIL", f"model {m['id']}", f"file missing: {m['file']}")
            continue
        if not lm._is_gguf(p):
            rec("FAIL", f"model {m['id']}", "not a GGUF (bad magic bytes)")
            continue
        mm = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", p.name)
        if mm:
            absent = [i for i in range(1, int(mm.group(2)) + 1)
                      if not p.with_name(p.name.replace(f"-{mm.group(1)}-of-", f"-{i:05d}-of-")).is_file()]
            if absent:
                rec("FAIL", f"model {m['id']}", f"split parts missing: {absent}")
                continue
        rec("PASS", f"model {m['id']}", f"{p.stat().st_size / 1e9:.1f} GB")
    extra = [f.name for f in (APP / "models").glob("*.gguf")
             if f.name.lower() not in referenced and not re.search(r"-0000[2-9]-of-", f.name)]
    rec("WARN" if extra else "PASS", "no unregistered .gguf files", ", ".join(extra))
    for role in ROLES:
        explicit = [m["id"] for m in lm.verified_models(role) if role in m.get("default_for", [])]
        if len(explicit) == 1:
            rec("PASS", f"default model for {role}", explicit[0])
        else:
            rec("FAIL", f"default model for {role}",
                f"need exactly one verified model with default_for '{role}', found {explicit}")
    emb = lm.verified_models(kind="embedding")
    rec("PASS" if emb else "WARN", "embedding model available (optional)", ", ".join(m["id"] for m in emb))
    if load:
        check_load(lm)


def check_load(lm):
    seen = set()
    srv = lm.LlamaServer(profile="cpu_16gb", port=8086)
    try:
        for role in ROLES:
            mid = lm.default_model_id(role)
            if not mid or mid in seen:
                continue
            seen.add(mid)
            m = next(x for x in lm.load_models() if x["id"] == mid)
            if m.get("prompt_format") == "completion":
                rec("WARN", f"load {mid}", "completion-format model, use llm_check.py")
                continue
            try:
                srv.start(mid)
                out = srv.chat([{"role": "user", "content": "Reply with exactly one word: READY"}], max_tokens=16)
                rec("PASS" if "ready" in out.lower() else "FAIL", f"load+answer {mid} (default for {role})", repr(out[:40]))
            except lm.LLMError as exc:
                rec("FAIL", f"load+answer {mid}", str(exc))
    finally:
        srv.stop()


def check_hardcoded_paths():
    hits = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in SCAN_SUFFIXES or p.stat().st_size > 2_000_000:
                continue
            if USER_PATH.search(p.read_text(encoding="utf-8", errors="replace")):
                hits.append(str(p.relative_to(ROOT)))
    rec("FAIL" if hits else "PASS", "no machine-specific user paths in suite files", "; ".join(hits[:8]))


def check_manifest(full):
    import make_manifest as mm
    if not mm.MANIFEST.is_file():
        rec("WARN", "MANIFEST.sha256", "not created yet: run application\\scripts\\make_manifest.py")
        return
    entries = mm.read_manifest()
    problems, hashed = [], 0
    for rel, (digest, size) in entries.items():
        p = ROOT / rel
        if not p.is_file():
            problems.append(f"missing {rel}")
        elif p.stat().st_size != size:
            problems.append(f"size changed {rel}")
        elif full or size <= 50 * 1024 * 1024:
            hashed += 1
            if mm.sha256_file(p) != digest:
                problems.append(f"hash changed {rel}")
    detail = f"{len(entries)} files listed, {hashed} hashed"
    if problems:
        rec("FAIL", "manifest integrity", f"{detail}; {len(problems)} problem(s): " + "; ".join(problems[:6])
            + " (if intentional, re-run make_manifest.py)")
    else:
        rec("PASS", "manifest integrity", detail + ("" if full else "; files over 50 MB checked by size only (use --full)"))
    unlisted = [rel for rel, _ in mm.iter_files() if rel not in entries]
    rec("WARN" if unlisted else "PASS", "no files outside the manifest",
        f"{len(unlisted)} unlisted, e.g. " + "; ".join(unlisted[:4]) if unlisted else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--load", action="store_true")
    a = ap.parse_args()
    print(f"Suite root: {ROOT}\n")
    for fn, args in ((check_structure, ()), (check_python, ()), (check_packages, ()),
                     (check_wheelhouse, (a.quick,)), (check_runtime, (a.quick,)), (check_ocr, (a.quick,)),
                     (check_models, (a.load,)), (check_hardcoded_paths, ()), (check_manifest, (a.full,))):
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            rec("FAIL", fn.__name__, f"unexpected error: {exc.__class__.__name__}: {exc}")
    fails, warns = STATUSES.count("FAIL"), STATUSES.count("WARN")
    print(f"\nSUMMARY: {STATUSES.count('PASS')} passed, {warns} warnings, {fails} failed")
    print("RESULT:", "NOT READY" if fails else "OK (offline behaviour still needs a manual no-network test)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())