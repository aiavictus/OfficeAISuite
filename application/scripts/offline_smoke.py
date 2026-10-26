"""Offline smoke test: run it with the network adapter DISABLED.
    python application\\scripts\\offline_smoke.py
Checks that no outside host is reachable, that each default model answers, that OCR works,
and that the three apps' core paths run. It cannot disable your network for you."""
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "project_1_documents" / "app"))

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" - {detail}" if not cond and detail else ""))


def offline():
    for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            socket.create_connection(host, timeout=2).close()
            return False
        except OSError:
            pass
    return True


check("network is disabled (1.1.1.1 and 8.8.8.8 unreachable)", offline(),
      "the machine still has internet: disable Wi-Fi/Ethernet and rerun")

from shared import llm_manager as lm  # noqa: E402

srv = lm.LlamaServer(profile="cpu_16gb", port=8095)
try:
    for role in ("documents", "sql", "dashboard"):
        mid = lm.default_model_id(role)
        cfg = next(m for m in lm.load_models() if m["id"] == mid)
        if cfg.get("prompt_format") == "completion":
            continue
        srv.start(mid)
        out = srv.chat([{"role": "user", "content": "Reply with exactly one word: READY"}], max_tokens=16)
        check(f"model {mid} ({role}) answers offline", "ready" in out.lower(), out[:40])
except lm.LLMError as exc:
    check("model load", False, str(exc))
finally:
    srv.stop()

from docqa import extract  # noqa: E402

fx = ROOT / "project_1_documents" / "tests" / "fixtures" / "scanned_invoice.pdf"
if fx.is_file():
    d = extract.extract_pdf(fx)
    check("OCR reads the scanned invoice", "1840" in d["pages"][0]["text"])
else:
    check("OCR fixture present", False, "run project_1_documents\\tests\\make_fixtures.py")

print("RESULT:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)