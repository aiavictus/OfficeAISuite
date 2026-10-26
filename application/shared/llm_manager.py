"""Runs ONE llama-server at a time (127.0.0.1 only) and talks to it.

Usage:
    srv = LlamaServer(profile="cpu_16gb", port=8081, log_dir=some_log_dir)
    srv.start("qwen3-4b")           # starts, waits until healthy
    text = srv.chat([{"role": "user", "content": "Hi"}], max_tokens=64)
    srv.start("gemma-3-4b-it")      # stops the previous model first
    srv.stop()
"""
import atexit
import json
import subprocess
import time
from pathlib import Path

import httpx

from .paths import APP_LOG_DIR, LLAMA_SERVER, MODELS_DIR, MODELS_JSON, PROFILES_JSON


class LLMError(RuntimeError):
    """Any failure to start, reach or use the local model server."""


def _is_gguf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def load_models() -> list:
    return json.loads(MODELS_JSON.read_text(encoding="utf-8"))["models"]


def verified_models(role=None, kind="chat") -> list:
    """Only models whose file exists and starts with the GGUF magic bytes."""
    out = []
    for m in load_models():
        if kind and m.get("kind", "chat") != kind:
            continue
        if role and role not in m.get("roles", []):
            continue
        p = MODELS_DIR / m["file"]
        if p.is_file() and _is_gguf(p):
            out.append(m)
    return out


def default_model_id(role):
    """The verified model marked default_for this role, else the first verified one, else None."""
    cands = verified_models(role)
    for m in cands:
        if role in m.get("default_for", []):
            return m["id"]
    return cands[0]["id"] if cands else None


class LlamaServer:
    def __init__(self, profile="cpu_16gb", port=None, log_dir=None):
        profiles = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))["profiles"]
        if profile not in profiles:
            raise LLMError(f"Unknown profile '{profile}'. Available: {', '.join(profiles)}")
        self.profile = profiles[profile]
        self.port = int(port or self.profile["port"])
        self.log_dir = Path(log_dir) if log_dir else APP_LOG_DIR
        self.base = f"http://127.0.0.1:{self.port}"
        self.model = None
        self._proc = None
        self._log = None
        atexit.register(self.stop)

    # ---- lifecycle -------------------------------------------------------
    def start(self, model_id, timeout=300):
        if self.model and self.model["id"] == model_id and self._proc and self._proc.poll() is None:
            return
        self.stop()
        m = next((x for x in load_models() if x["id"] == model_id), None)
        if m is None:
            raise LLMError(f"Unknown model id '{model_id}'")
        path = MODELS_DIR / m["file"]
        if not path.is_file() or not _is_gguf(path):
            raise LLMError(f"Model file missing or not a GGUF: {m['file']}")
        if not LLAMA_SERVER.is_file():
            raise LLMError("llama-server.exe not found in application\\llm_runtime\\llama.cpp")
        if self._port_in_use():
            raise LLMError(f"Port {self.port} is already in use. Stop the other llama-server or change the port.")

        p = self.profile
        cmd = [str(LLAMA_SERVER), "-m", str(path), "--host", "127.0.0.1", "--port", str(self.port),
               "-c", str(p["ctx"]), "-t", str(p["threads"]), "-tb", str(p["threads_batch"]),
               "-b", str(p["batch"]), "-np", "1", "-ngl", str(p["gpu_layers"])] + m.get("extra_args", [])
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_dir / f"llama_{m['id']}.log", "wb")
        self._proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT,
                                      cwd=str(LLAMA_SERVER.parent))
        self.model = m
        t0 = time.time()
        with httpx.Client(trust_env=False, timeout=10) as c:
            while True:
                if self._proc.poll() is not None:
                    code = self._proc.returncode
                    self.stop()
                    raise LLMError(f"llama-server exited during load (code {code}). See log in {self.log_dir}")
                if time.time() - t0 > timeout:
                    self.stop()
                    raise LLMError(f"Model not ready after {timeout}s")
                try:
                    if c.get(self.base + "/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(1)

    def stop(self):
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
        self.model = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def _port_in_use(self) -> bool:
        import socket
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    # ---- requests --------------------------------------------------------
    def _need_running(self):
        if not self.model or not self._proc or self._proc.poll() is not None:
            raise LLMError("No model is running. Call start(model_id) first.")

    def chat(self, messages, max_tokens=512, temperature=0.0, timeout=600) -> str:
        """Chat completion. Thinking is disabled for models flagged no_think."""
        self._need_running()
        body = {"messages": messages, "max_tokens": int(max_tokens), "temperature": temperature}
        if self.model.get("no_think"):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            with httpx.Client(trust_env=False, timeout=timeout) as c:
                r = c.post(self.base + "/v1/chat/completions", json=body)
                r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Model request failed: {exc}") from exc
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not text:
            raise LLMError("The model returned an empty answer.")
        return text

    def complete(self, prompt, max_tokens=256, stop=None, timeout=600) -> str:
        """Raw completion, for models such as SQLCoder that need their own prompt format."""
        self._need_running()
        body = {"prompt": prompt, "n_predict": int(max_tokens), "temperature": 0, "stop": stop or []}
        try:
            with httpx.Client(trust_env=False, timeout=timeout) as c:
                r = c.post(self.base + "/completion", json=body)
                r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Model request failed: {exc}") from exc
        return (r.json().get("content") or "").strip()