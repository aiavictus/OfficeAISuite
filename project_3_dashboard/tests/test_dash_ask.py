import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp import ask  # noqa: E402
from dashapp.profile import load_file  # noqa: E402
from dashapp.server import create_app  # noqa: E402


class Fake:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def start(self, m): pass

    def chat(self, messages, **kw):
        self.sent.append(messages)
        return self.reply


GOOD = {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}]}


@pytest.fixture
def env(tmp_path):
    f = fx.build_all(tmp_path / "fx")

    def make(reply):
        llm = Fake(reply)
        c = TestClient(create_app(tmp_path / "ws" / "a", llm=llm))
        h = {"X-Session-Token": c.post("/api/session").json()["token"]}
        b = c.post("/api/batch", headers=h).json()["batch"]
        c.post("/api/upload", params={"batch": b}, headers=h, files={"file": ("store_b.csv", io.BytesIO(f["store_b"].read_bytes()))})
        i = c.get("/api/files", params={"batch": b}, headers=h).json()["files"][0]["id"]
        return c, h, {"batch": b, "file_id": i, "sheet": "data", "request": "units by category"}, llm
    return make, f


def test_extract_json_variants():
    assert ask.extract_json(json.dumps(GOOD)) == GOOD
    assert ask.extract_json("```json\n" + json.dumps(GOOD) + "\n```") == GOOD
    assert ask.extract_json("Sure! Here: " + json.dumps(GOOD) + " hope it helps") == GOOD
    for bad in ("", "no json here", "{broken", "[1, 2]"):
        with pytest.raises(ask.AskError):
            ask.extract_json(bad)


def test_model_sees_metadata_but_never_rows(env):
    _, f = env
    df = load_file(f["store_b"], "csv")[0][0]["df"]
    text = ask.describe(df)
    assert "Category (categorical)" in text and "Toys" in text and "Units (numeric)" in text
    rows = fx.store_b_rows()
    # a row-level value that is neither a min/max nor a category value must not appear
    assert str(rows[1][3]) + " " not in text.replace("range: 2 to 10", "")
    assert "2025-01-08" not in text          # second row's date: only the first and last dates appear (as the range)
    assert text.count("\n") == 4             # one line per column, never one line per row

def test_good_plan_runs_and_numbers_come_from_pandas(env):
    make, _ = env
    c, h, body, _ = make(json.dumps(GOOD))
    r = c.post("/api/ask", headers=h, json=body)
    assert r.status_code == 200
    exp = {}
    for row in fx.store_b_rows():
        exp[row[1]] = exp.get(row[1], 0) + row[3]
    assert {x[0]: x[1] for x in r.json()["rows"]} == exp and r.json()["plan"] == GOOD


@pytest.mark.parametrize("reply", [
    '{"group_by": ["Nope"]}', '{"filters": [{"column": "Region", "op": "eq", "value": "West"}]}',
    '{"aggregations": [{"column": "Category", "agg": "sum"}]}', "not json at all",
    '{"__import__": "os"}', "DROP TABLE x", '{"chart": "scatter", "x": "Category", "y": "Units"}',
])
def test_bad_model_plans_are_refused_with_a_reason(env, reply):
    make, _ = env
    c, h, body, _ = make(reply)
    r = c.post("/api/ask", headers=h, json=body)
    assert r.status_code == 422 and r.json()["detail"]


def test_cannot_answer_and_input_limits(env):
    make, _ = env
    c, h, body, llm = make('{"cannot_answer": true}')
    assert c.post("/api/ask", headers=h, json=body).status_code == 422
    assert c.post("/api/ask", headers=h, json={**body, "request": ""}).status_code == 422
    assert c.post("/api/ask", headers=h, json={**body, "request": "q" * 401}).status_code == 422
    assert c.post("/api/ask", headers=h, json={**body, "model_id": "nope"}).status_code == 400
    assert c.post("/api/ask", headers=h, json={**body, "file_id": "x" * 32}).status_code == 409


def test_old_batch_cannot_be_asked_about(env):
    make, _ = env
    c, h, body, _ = make(json.dumps(GOOD))
    c.post("/api/batch", headers=h)
    assert c.post("/api/ask", headers=h, json=body).status_code == 409