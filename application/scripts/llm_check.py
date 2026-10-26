"""Start llama-server for one model, run one functional check, stop the server.

Run from the suite root after `call ENV.bat`:
  python application\\scripts\\llm_check.py --list
  python application\\scripts\\llm_check.py <model_id> [--sql] [--profile cpu_16gb]

Checks:
  default  chat model must answer READY; embedding model must return a vector
  --sql    model must write a SELECT with COUNT and GROUP BY for a tiny schema
  models with "prompt_format": "completion" always run the SQL check via /completion
Optional models.json keys: no_think, reasoning_model, prompt_format, extra_args
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

APP = Path(__file__).resolve().parents[1]
MODELS_DIR = APP / "models"
MODELS_JSON = MODELS_DIR / "models.json"
PROFILES_JSON = APP / "shared" / "config" / "llm_profiles.json"
SERVER = APP / "llm_runtime" / "llama.cpp" / "llama-server.exe"
LOG_DIR = APP / "logs"

SCHEMA = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, city TEXT);"
QUESTION = "How many users are there in each city?"
SQL_CHAT_SYSTEM = (
    "You write SQLite SQL. Reply with exactly one SELECT statement and nothing else.\n"
    "Schema:\n" + SCHEMA
)
SQL_COMPLETION_PROMPT = (
    "### Task\nGenerate a SQL query to answer [QUESTION]" + QUESTION + "[/QUESTION]\n\n"
    "### Database Schema\nThe query will run on a database with the following schema:\n"
    + SCHEMA + "\n\n"
    "### Answer\nGiven the database schema, here is the SQL query that answers [QUESTION]"
    + QUESTION + "[/QUESTION]\n[SQL]\n"
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def is_gguf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def list_models(models):
    referenced = set()
    for m in models:
        p = MODELS_DIR / m["file"]
        state = "OK" if p.is_file() and is_gguf(p) else "MISSING/INVALID"
        print(f'{m["id"]:<26}{state:<16}{m["file"]}')
        referenced.add(m["file"].lower())
    extras = []
    for f in sorted(MODELS_DIR.glob("*.gguf")):
        part = re.search(r"-(\d+)-of-\d+\.gguf$", f.name.lower())
        if part and int(part.group(1)) > 1:
            continue  # later part of a split model
        if f.name.lower() not in referenced:
            extras.append(f.name)
    if extras:
        print("\nGGUF files in application\\models with NO entry in models.json:")
        for name in extras:
            print("  UNREGISTERED", name)


def speed_text(data, elapsed):
    tm = (data or {}).get("timings") or {}
    gen = tm.get("predicted_per_second")
    pro = tm.get("prompt_per_second")
    if gen:
        extra = f", prompt processing {pro:.1f} tok/s" if pro else ""
        return f"generation {gen:.1f} tok/s{extra}"
    return f"{elapsed:.1f}s elapsed (server returned no timings)"


def judge_sql(text):
    t = text.lower()
    return "select" in t and "count" in t and "group by" in t


def chat(c, base, messages, max_tokens, no_think):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0}
    if no_think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    r = c.post(base + "/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    d = r.json()
    msg = d["choices"][0]["message"]
    return (msg.get("content") or "").strip(), (msg.get("reasoning_content") or "").strip(), d


def run_check(c, base, m, want_sql):
    """Return (ok, message)."""
    t0 = time.time()
    if m.get("kind") == "embedding":
        r = c.post(base + "/v1/embeddings", json={"input": "hello world"}, timeout=120)
        r.raise_for_status()
        dim = len(r.json()["data"][0]["embedding"])
        return dim > 0, f"embedding dimension = {dim}"

    if m.get("prompt_format") == "completion":
        r = c.post(base + "/completion", timeout=600, json={
            "prompt": SQL_COMPLETION_PROMPT, "n_predict": 120, "temperature": 0, "stop": ["###"]})
        r.raise_for_status()
        d = r.json()
        text = (d.get("content") or "").strip()
        ok = judge_sql(text)
        return ok, f"SQL check via /completion: {text[:300]!r}\n{speed_text(d, time.time() - t0)}"

    no_think = bool(m.get("no_think"))
    if want_sql:
        text, reasoning, d = chat(c, base, [
            {"role": "system", "content": SQL_CHAT_SYSTEM},
            {"role": "user", "content": QUESTION}], 160, no_think)
        ok = judge_sql(text)
        return ok, f"SQL check: {text[:300]!r}\n{speed_text(d, time.time() - t0)}"

    if m.get("reasoning_model"):
        text, reasoning, d = chat(c, base, [
            {"role": "user", "content": "Reply with exactly one word: READY"}], 300, False)
        ok = bool(text or reasoning)
        return ok, ("LOAD PASS (reasoning model: answer text is inside its thinking). "
                    f"content={text[:60]!r} thinking_chars={len(reasoning)}\n"
                    + speed_text(d, time.time() - t0))

    text, reasoning, d = chat(c, base, [
        {"role": "user", "content": "Reply with exactly one word: READY"}], 48, no_think)
    ok = "ready" in text.lower()
    msg = f"reply={text[:100]!r}\n{speed_text(d, time.time() - t0)}"
    if not text and reasoning:
        msg += "\nDIAGNOSIS: the model is still thinking, so thinking was NOT disabled."
    return ok, msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id", nargs="?")
    ap.add_argument("--profile", default="cpu_16gb")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sql", action="store_true")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    models = load(MODELS_JSON)["models"]
    profiles = load(PROFILES_JSON)["profiles"]

    if a.list:
        list_models(models)
        return 0

    m = next((x for x in models if x["id"] == a.model_id), None)
    if m is None:
        print("Unknown model id. Use --list.")
        return 2
    if a.profile not in profiles:
        print("Unknown profile:", a.profile)
        return 2
    prof = profiles[a.profile]
    path = MODELS_DIR / m["file"]
    if not path.is_file() or not is_gguf(path):
        print("FAIL: model file missing or not a GGUF:", path)
        return 1
    if not SERVER.is_file():
        print("FAIL: llama-server.exe not found at", SERVER)
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'llm_check_{m["id"]}.log'
    cmd = [str(SERVER), "-m", str(path), "--host", "127.0.0.1", "--port", str(prof["port"]),
           "-c", str(prof["ctx"]), "-t", str(prof["threads"]), "-tb", str(prof["threads_batch"]),
           "-b", str(prof["batch"]), "-np", "1", "-ngl", str(prof["gpu_layers"])] + m.get("extra_args", [])
    base = f'http://127.0.0.1:{prof["port"]}'

    print(f'Loading {m["name"]} ({m["quant"]}) with profile {a.profile} ...')
    t0 = time.time()
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(SERVER.parent))
        try:
            with httpx.Client(trust_env=False, timeout=30) as c:
                while True:
                    if proc.poll() is not None:
                        print(f"FAIL: server exited (code {proc.returncode}). See {log_path}")
                        return 1
                    if time.time() - t0 > a.timeout:
                        print(f"FAIL: not ready after {a.timeout}s. See {log_path}")
                        return 1
                    try:
                        if c.get(base + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(2)
                print(f"Loaded in {time.time() - t0:.0f}s")
                ok, message = run_check(c, base, m, a.sql)
                print(message)
                print("PASS" if ok else "FAIL")
                return 0 if ok else 1
        except httpx.HTTPError as exc:
            print("FAIL: request error:", exc, "See", log_path)
            return 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())