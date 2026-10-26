"""Run the text questions in fixtures/questions.json through a real local model.

    python project_1_documents\\tests\\benchmark_docs.py qwen3-4b
    python project_1_documents\\tests\\benchmark_docs.py qwen3-8b google-gemma-3-4b-it

Pass criteria per question:
  number/text  answer status must be calculated or sourced, contain the expected value,
               and cite the expected file
  unanswerable status must be insufficient (or an invalid reply that the validator rejected)
Reports pass counts and time per model. Repeat runs to see variation: LLM output is not deterministic.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docqa import answer, extract as ex  # noqa: E402
from docqa.registry import FileRecord  # noqa: E402
from shared.llm_manager import LLMError, LlamaServer  # noqa: E402

FIX = HERE.parent / "fixtures"


def load_all(tmp):
    out = {}
    for f in FIX.iterdir():
        if f.suffix.lower() in (".pdf", ".xlsx", ".csv"):
            dst = Path(tmp) / f.name
            shutil.copyfile(f, dst)
            sha = hashlib.sha256(dst.read_bytes()).hexdigest()
            r = FileRecord("repo:" + f.name, f.name, f.suffix[1:].lower(), dst.stat().st_size, 0, sha, 1, dst, "repository")
            out[f.name] = ex.extract_file(r, Path(tmp) / "cache")
    return out


def norm(s):
    return str(s).replace(",", "").replace(" ", "").lower()


def _first_number(text):
    import re
    m = re.search(r"-?\d[\d,]*\.?\d*", str(text))
    return float(m.group().replace(",", "")) if m else None


def judge(q, a):
    if q["type"] == "unanswerable":
        return a.status in ("insufficient", "invalid"), a.status
    if a.status not in ("calculated", "sourced"):
        return False, f"{a.status}: {a.detail or a.text[:60]}"
    exp = q["expected"]
    if isinstance(exp, (int, float)):
        got = _first_number(a.text)
        if got is None or abs(got - exp) > max(0.01, abs(exp) * 1e-4):
            return False, f"wrong answer: {a.text[:80]!r} (expected {exp:.2f})"
    elif norm(exp) not in norm(a.text):
        return False, f"wrong answer: {a.text[:80]!r}"
    cited = " ".join(a.citations).lower()
    if q["type"] != "number" and q.get("cite") and q["cite"].split(" ")[0].lower() not in cited:
        return False, f"wrong citation: {a.citations}"
    return True, "ok"


def main(models):
    qs = json.loads((FIX / "questions.json").read_text(encoding="utf-8"))["questions"]
    srv = LlamaServer(profile="cpu_16gb", port=8087)
    try:
        for mid in models:
            tmp = tempfile.mkdtemp()
            data = load_all(tmp)
            passed, total, t0 = 0, 0, time.time()
            print(f"\n=== {mid} ===")
            for q in qs:
                total += 1
                try:
                    a = answer.answer_question(q["question"], [data[f] for f in q["files"]], srv, mid)
                    ok, why = judge(q, a)
                except LLMError as exc:
                    ok, why = False, f"LLM error: {exc}"
                passed += ok
                print(f"[{'PASS' if ok else 'FAIL'}] {q['id']:<4} {q['question'][:52]:<52} {why}")
            print(f"--> {mid}: {passed}/{total} passed in {time.time() - t0:.0f}s")
            srv.stop()
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        srv.stop()


if __name__ == "__main__":
    main(sys.argv[1:] or ["qwen3-4b"])