"""Real-model benchmark of free-text requests. Expected results are computed in plain Python.
    python project_3_dashboard\\tests\\benchmark_ask.py qwen3-4b qwen3-8b google-gemma-3-4b-it qwen2.5-coder-7b
"""
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp import ask, engine  # noqa: E402
from dashapp.profile import load_file  # noqa: E402
from shared.llm_manager import LLMError, LlamaServer  # noqa: E402

R = fx.orders_rows()                # (date, category, region|None, units, revenue|None)


def by(key, val, filt=lambda r: True):
    d = defaultdict(float)
    for r in R:
        if filt(r) and r[val] is not None:
            d[key(r)] += r[val]
    return dict(d)


def groups(res):
    return {str(r[0]): r[1] for r in res["rows"]}


def single(res):
    return res["rows"][0][0]


CASES = [
    ("total units by category", lambda x: groups(x) == by(lambda r: r[1], 3)),
    ("total revenue by region", lambda x: {k: v for k, v in groups(x).items() if k != "(missing)"} == by(lambda r: r[2], 4, lambda r: r[2])),
    ("how many rows are there", lambda x: single(x) == len(R)),
    ("total units", lambda x: single(x) == sum(r[3] for r in R)),
    ("average units", lambda x: abs(single(x) - sum(r[3] for r in R) / len(R)) < 1e-6),
    ("total units for the North region", lambda x: single(x) == sum(r[3] for r in R if r[2] == "North")),
    ("total units by month", lambda x: groups(x) == by(lambda r: r[0].strftime("%Y-%m"), 3)),
    ("top 2 categories by total units", lambda x: len(x["rows"]) == 2 and x["rows"][0][1] == max(by(lambda r: r[1], 3).values())),
    ("number of rows by category", lambda x: groups(x) == {k: float(sum(1 for r in R if r[1] == k)) for k in {r[1] for r in R}}),
    ("highest revenue", lambda x: single(x) == max(r[4] for r in R if r[4] is not None)),
    ("total revenue in the West region", None),      # must be refused
    ("how many customers are unhappy", None),        # must be refused
]


def main(models):
    tmp = Path(tempfile.mkdtemp())
    fx.build_all(tmp)
    df = load_file(tmp / "orders_2025.xlsx", "xlsx")[0][0]["df"]
    srv = LlamaServer(profile="cpu_16gb", port=8089)
    try:
        for mid in models:
            passed, t0 = 0, time.time()
            print(f"\n=== {mid} ===")
            for text, check in CASES:
                try:
                    plan = ask.plan_from_request(text, df, srv, mid)
                    if check is None:
                        ok, why = plan is None, "answered" if plan is not None else "refused"
                    elif plan is None:
                        ok, why = False, "cannot_answer"
                    else:
                        ok = bool(check(engine.execute(df, plan)))
                        why = "ok" if ok else "wrong result: " + str(plan)[:90]
                except ask.AskError as exc:
                    ok, why = check is None, f"refused: {str(exc)[:70]}"
                except (LLMError, Exception) as exc:  # noqa: BLE001
                    ok, why = False, f"error: {str(exc)[:70]}"
                passed += ok
                print(f"[{'PASS' if ok else 'FAIL'}] {text:<38} {why}")
            print(f"--> {mid}: {passed}/{len(CASES)} in {time.time() - t0:.0f}s")
            srv.stop()
    finally:
        srv.stop()


if __name__ == "__main__":
    main(sys.argv[1:] or ["qwen3-4b"])