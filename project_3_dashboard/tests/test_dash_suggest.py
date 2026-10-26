import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp import engine, suggest  # noqa: E402
from dashapp.profile import load_file  # noqa: E402


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    f = fx.build_all(tmp_path_factory.mktemp("s"))
    first = lambda p, k: load_file(p, k)[0][0]["df"]      # noqa: E731
    return {"orders": first(f["orders"], "xlsx"), "store": first(f["store_b"], "csv"), "messy": first(f["messy"], "csv")}


@pytest.mark.parametrize("name", ["orders", "store", "messy"])
def test_every_suggestion_validates_and_runs(data, name):
    sugg = suggest.suggest(data[name])
    assert sugg and len({s["title"] for s in sugg}) == len(sugg)
    for s in sugg:
        res = engine.execute(data[name], s["plan"])
        assert res["columns"] and res["description"]


def test_expected_suggestions_for_the_orders_table(data):
    sugg = suggest.suggest(data["orders"])
    kinds = [engine.execute(data["orders"], s["plan"])["chart"]["type"] for s in sugg]
    assert "line" in kinds and "bar" in kinds and "scatter" in kinds and "metric" in kinds and "table" in kinds
    assert suggest.suggest(data["orders"], limit=3).__len__() == 3


def test_id_like_columns_are_not_offered_as_measures(data):
    for s in suggest.suggest(data["messy"]):
        assert all(a["column"] != "id" for a in s["plan"].get("aggregations", []))
        assert s["plan"].get("x") != "id"


def test_shared_columns_and_comparison_suggestions(data):
    tables = [{"name": "orders", "df": data["orders"]}, {"name": "store", "df": data["store"]}]
    shared = {s["column"]: s for s in suggest.shared_columns(tables)}
    assert {"Date", "Category", "Region", "Units", "Revenue"} <= set(shared)
    assert shared["Revenue"]["role"] == "measure" and shared["Date"]["role"] == "date"
    assert suggest.shared_columns([tables[0], {"name": "messy", "df": data["messy"]}]) == []
    sugg = suggest.comparison_suggestions(tables)
    assert sugg
    kinds = [engine.compare(tables, s["plan"])["chart"]["type"] for s in sugg]
    assert "line" in kinds and "bar" in kinds


def test_comparison_suggestions_use_only_columns_every_file_has(data):
    three = [{"name": "orders", "df": data["orders"]}, {"name": "store", "df": data["store"]},
             {"name": "messy", "df": data["messy"]}]
    assert suggest.comparison_suggestions(three) == []