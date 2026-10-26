"""Live smoke test: start one model, ask it something, switch models, stop.
Run from the suite root after call ENV.bat:  python application\\scripts\\llm_manager_live.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.llm_manager import LlamaServer, LLMError  # noqa: E402


def main():
    srv = LlamaServer(profile="cpu_16gb", port=8085)
    try:
        for mid in ("qwen3-4b", "google-gemma-3-4b-it"):
            srv.start(mid)
            out = srv.chat([{"role": "user", "content": "Reply with exactly one word: READY"}], max_tokens=16)
            print(f"{mid}: {out!r}")
            if "ready" not in out.lower():
                print("FAIL")
                return 1
        srv.stop()
        print("PASS: started, answered, switched models, stopped")
        return 0
    except LLMError as exc:
        print("FAIL:", exc)
        return 1
    finally:
        srv.stop()


if __name__ == "__main__":
    sys.exit(main())