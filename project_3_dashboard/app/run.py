"""Start Project 3 on 127.0.0.1 only.  Usage: python project_3_dashboard\\app\\run.py [--port 8110]"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parent))

import uvicorn  # noqa: E402

from dashapp.server import create_app  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8110)
    a = ap.parse_args()
    print(f"Project 3 running at http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(port=a.port), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()