import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp import config, engine  # noqa: E402
from dashapp.plan import MAX_IN, PlanError  # noqa: E402
from dashapp.profile import load_file  # noqa: E402

ROWS = fx.orders_rows()          # (date, category, region|None, units, revenue|None)
STORE = fx.store_b_rows()


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    f = fx.build_all(tmp_path_factory.mktemp("e"))
    first = lambda p, k: load_file(p, k)[0][0]["df"]      # noqa: E731
    return {"orders": first(f["orders"], "xlsx"), "store": first(f["store_b"], "csv"), "messy": first(f["messy"], "csv")}


def sums(rows, key, val, filt=lambda r: True):
    out = defaultdict(float)
    for r in rows:
        if filt(r) and r[val] is not None:
            out[key(r)] += r[val]
    return out


def table(res):
    return {r[0]: r[1] for r in res["rows"]}


# ---------------- aggregation correctness (expected values computed independently) ----------------
def test_group_sum_matches_independent_calculation_and_default_sort(data):
    res = engine.execute(data["orders"], {"group_by": ["category"], "aggregations": [{"column": "Revenue", "agg": "sum"}]})
    assert res["columns"] == ["Category", "Revenue_sum"]
    assert table(res) == pytest.approx(dict(sums(ROWS, lambda r: r[1], 4)))
    vals = [r[1] for r in res["rows"]]
    assert vals == sorted(vals, reverse=True)


def test_filter_with_four_aggregations_and_case_insensitive_value(data):
    sel = [r for r in ROWS if r[2] == "North"]
    res = engine.execute(data["orders"], {
        "filters": [{"column": "region", "op": "eq", "value": "north"}],
        "aggregations": [{"column": "Units", "agg": "sum"}, {"column": "Units", "agg": "mean"},
                         {"column": "*", "agg": "count"}, {"column": "Category", "agg": "nunique"}]})
    assert res["columns"] == ["Units_sum", "Units_mean", "row_count", "Category_nunique"]
    got = res["rows"][0]
    assert got == pytest.approx([sum(r[3] for r in sel), sum(r[3] for r in sel) / len(sel), len(sel),
                                 len({r[1] for r in sel})])
    assert res["rows_matched"] == len(sel) and res["rows_total"] == len(ROWS) and res["chart"]["type"] == "metric"


def test_count_column_ignores_nulls_but_star_counts_rows(data):
    res = engine.execute(data["orders"], {"aggregations": [{"column": "Revenue", "agg": "count"},
                                                           {"column": "*", "agg": "count"},
                                                           {"column": "Revenue", "agg": "min"},
                                                           {"column": "Revenue", "agg": "max"}]})
    revs = [r[4] for r in ROWS if r[4] is not None]
    assert res["rows"][0] == pytest.approx([len(revs), len(ROWS), min(revs), max(revs)])


def test_month_buckets_sorted_by_time_and_line_chart(data):
    exp = sums(ROWS, lambda r: r[0].strftime("%Y-%m"), 3)
    res = engine.execute(data["orders"], {"group_by": [{"column": "Date", "bucket": "month"}],
                                          "aggregations": [{"column": "Units", "agg": "sum"}]})
    assert table(res) == pytest.approx(dict(exp))
    assert [r[0] for r in res["rows"]] == sorted(exp) and res["chart"]["type"] == "line" and res["chart"]["x"] == "Date"


def test_quarter_and_year_labels(data):
    q = engine.execute(data["orders"], {"group_by": [{"column": "Date", "bucket": "quarter"}],
                                        "aggregations": [{"column": "Units", "agg": "sum"}]})
    exp = sums(ROWS, lambda r: f"{r[0].year}-Q{(r[0].month - 1) // 3 + 1}", 3)
    assert table(q) == pytest.approx(dict(exp))
    y = engine.execute(data["orders"], {"group_by": [{"column": "Date", "bucket": "year"}],
                                        "aggregations": [{"column": "Units", "agg": "sum"}]})
    assert table(y) == {"2025": sum(r[3] for r in ROWS)}


def test_missing_group_values_are_shown_not_dropped(data):
    res = engine.execute(data["orders"], {"group_by": ["Region"], "aggregations": [{"column": "*", "agg": "count"}]})
    got = table(res)
    assert got["(missing)"] == sum(1 for r in ROWS if r[2] is None) and sum(got.values()) == len(ROWS)


def test_two_groups_use_a_series(data):
    res = engine.execute(data["orders"], {"group_by": ["Category", "Region"], "aggregations": [{"column": "Units", "agg": "sum"}]})
    exp = defaultdict(float)
    for r in ROWS:
        exp[(r[1], r[2] or "(missing)")] += r[3]
    assert {(r[0], r[1]): r[2] for r in res["rows"]} == pytest.approx(dict(exp))
    assert res["chart"] == {"type": "bar", "x": "Category", "y": ["Units_sum"], "series": "Region"}


def test_sort_and_top_n(data):
    exp = sorted(sums(ROWS, lambda r: r[1], 3).items(), key=lambda kv: -kv[1])[:2]
    res = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}],
                                          "sort": {"by": "units_sum", "desc": True}, "top_n": 2})
    assert [(r[0], r[1]) for r in res["rows"]] == pytest.approx(exp) or [r[0] for r in res["rows"]] == [k for k, _ in exp]
    assert len(res["rows"]) == 2
    low = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}],
                                          "sort": {"by": "Units_sum", "desc": False}})
    vals = [r[1] for r in low["rows"]]
    assert vals == sorted(vals)


@pytest.mark.parametrize("flt,keep", [
    ({"column": "Date", "op": "between", "value": ["2025-02-01", "2025-02-28"]},
     lambda r: datetime(2025, 2, 1) <= r[0] <= datetime(2025, 2, 28)),
    ({"column": "Units", "op": "gt", "value": 10}, lambda r: r[3] > 10),
    ({"column": "Region", "op": "is_null", "value": None}, lambda r: r[2] is None),
    ({"column": "Category", "op": "in", "value": ["toys", "Grocery"]}, lambda r: r[1] in ("Toys", "Grocery")),
    ({"column": "Region", "op": "ne", "value": "North"}, lambda r: r[2] == "South"),     # ne leaves out empty cells
    ({"column": "Revenue", "op": "not_null", "value": None}, lambda r: r[4] is not None),
])
def test_filter_operations(data, flt, keep):
    res = engine.execute(data["orders"], {"filters": [flt], "aggregations": [{"column": "*", "agg": "count"}]})
    assert res["rows"][0][0] == sum(1 for r in ROWS if keep(r))


def test_table_mode_columns_and_truncation(data, monkeypatch):
    monkeypatch.setattr(config, "TABLE_PREVIEW_ROWS", 5)
    res = engine.execute(data["orders"], {"columns": ["category", "Units"]})
    assert res["columns"] == ["Category", "Units"] and len(res["rows"]) == 5 and res["truncated"]
    assert res["rows"][0] == [ROWS[0][1], ROWS[0][3]] and res["chart"]["type"] == "table"


def test_scatter_uses_only_complete_pairs(data):
    res = engine.execute(data["orders"], {"chart": "scatter", "x": "Units", "y": "Revenue"})
    assert len(res["rows"]) == sum(1 for r in ROWS if r[4] is not None) and res["chart"]["type"] == "scatter"
    assert any("missing" in n for n in res["notes"])


def test_chart_requests_that_fit_and_that_do_not(data):
    pie = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}], "chart": "pie"})
    assert pie["chart"]["type"] == "pie" and not pie["notes"]
    cat_line = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}], "chart": "line"})
    assert cat_line["chart"]["type"] == "bar" and any("line" in n for n in cat_line["notes"])
    date_pie = engine.execute(data["orders"], {"group_by": [{"column": "Date", "bucket": "day"}],
                                               "aggregations": [{"column": "Units", "agg": "mean"}], "chart": "pie"})
    assert date_pie["chart"]["type"] != "pie"
    metric_bar = engine.execute(data["orders"], {"aggregations": [{"column": "Units", "agg": "sum"}], "chart": "bar"})
    assert metric_bar["chart"]["type"] == "metric" and metric_bar["notes"]


def test_metrics_overall_totals_ignore_top_n(data):
    res = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}], "top_n": 1})
    overall = next(m for m in res["metrics"] if m["label"].startswith("Overall"))
    assert overall["value"] == sum(r[3] for r in ROWS) and len(res["rows"]) == 1


def test_unknown_value_strict_rejected_but_lenient_returns_empty_with_note(data):
    plan = {"filters": [{"column": "Region", "op": "eq", "value": "West"}], "group_by": ["Category"],
            "aggregations": [{"column": "Units", "agg": "sum"}]}
    with pytest.raises(PlanError) as e:
        engine.execute(data["orders"], plan)
    assert "West" in str(e.value) and "North" in str(e.value)
    res = engine.execute(data["orders"], plan, strict_values=False)
    assert res["rows"] == [] and any("No rows match" in n for n in res["notes"])


def test_result_is_json_safe_and_input_not_modified(data):
    df = data["orders"]
    before = df.copy()
    for plan in ({"aggregations": [{"column": "Date", "agg": "min"}, {"column": "Date", "agg": "max"}]},
                 {"group_by": [{"column": "Date", "bucket": "month"}], "aggregations": [{"column": "Revenue", "agg": "mean"}]},
                 {"chart": "table"}):
        json.dumps(engine.execute(df, plan))
    pd.testing.assert_frame_equal(df, before)
    first = engine.execute(df, {"aggregations": [{"column": "Date", "agg": "min"}]})
    assert first["rows"][0][0] == ROWS[0][0].isoformat()


def test_description_is_generated_from_the_plan(data):
    res = engine.execute(data["orders"], {"filters": [{"column": "Region", "op": "eq", "value": "North"}],
                                          "group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}], "top_n": 2})
    d = res["description"]
    assert "Total of Units by Category" in d and "Region is North" in d and "Top 2" in d


# ---------------- validation: every one of these must be refused ----------------
BAD = [
    "x", None, [], {"drop": 1},
    {"group_by": ["Nope"]}, {"group_by": ["Units; DROP TABLE x"]},
    {"aggregations": [{"column": "Category", "agg": "sum"}]}, {"aggregations": [{"column": "Category", "agg": "min"}]},
    {"aggregations": [{"column": "*", "agg": "sum"}]}, {"aggregations": [{"column": "Units", "agg": "median"}]},
    {"aggregations": [{"column": "Units", "agg": "sum", "alias": "x;y"}]},
    {"aggregations": [{"column": "Units", "agg": "sum"}, {"column": "Units", "agg": "sum"}]},
    {"aggregations": [{"column": "Units", "agg": "sum", "alias": f"a{i}"} for i in range(5)]},
    {"group_by": [{"column": "Category", "bucket": "month"}]}, {"group_by": ["Date"]},
    {"group_by": [{"column": "Date", "bucket": "week"}]}, {"group_by": ["Category", "Region", "Units"]},
    {"group_by": ["Category", "category"]},
    {"filters": [{"column": "Region", "op": "regex", "value": "N.*"}]},
    {"filters": [{"column": "Region", "op": "gt", "value": "A"}]},
    {"filters": [{"column": "Region", "op": "eq", "value": "West"}]},
    {"filters": [{"column": "Units", "op": "between", "value": [5]}]},
    {"filters": [{"column": "Units", "op": "between", "value": [9, 5]}]},
    {"filters": [{"column": "Units", "op": "gt", "value": "abc"}]},
    {"filters": [{"column": "Units", "op": "gt", "value": True}]},
    {"filters": [{"column": "Category", "op": "in", "value": []}]},
    {"filters": [{"column": "Units", "op": "in", "value": list(range(MAX_IN + 1))}]},
    {"filters": [{"column": "Date", "op": "gt", "value": "not a date"}]},
    {"filters": [{"column": "Units", "op": "gt", "value": 1, "extra": 1}]},
    {"filters": [{"column": "Units", "op": "gt", "value": 1}] * 9},
    {"group_by": ["Category"], "top_n": 0}, {"group_by": ["Category"], "top_n": 101},
    {"group_by": ["Category"], "top_n": True}, {"group_by": ["Category"], "top_n": "5"},
    {"group_by": ["Category"], "sort": {"by": "Nope"}},
    {"group_by": ["Category"], "sort": {"by": "Category", "desc": "yes"}},
    {"chart": "scatter", "x": "Units"}, {"chart": "scatter", "x": "Category", "y": "Units"},
    {"chart": "scatter", "x": "Units", "y": "Revenue", "group_by": ["Category"]},
    {"columns": ["Nope"]}, {"columns": ["Units"], "sort": {"by": "Units"}}, {"columns": []},
    {"x": "Units"}, {"chart": "donut"},
]


@pytest.mark.parametrize("plan", BAD, ids=lambda p: json.dumps(p, default=str)[:70])
def test_bad_plans_are_refused(data, plan):
    with pytest.raises(PlanError):
        engine.execute(data["orders"], plan)


# ---------------- comparing files ----------------
def two(data):
    return [{"name": "orders", "df": data["orders"]}, {"name": "store", "df": data["store"]}]


def test_compare_by_category_matches_each_file(data):
    res = engine.compare(two(data), {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}]})
    exp = {("orders", k): v for k, v in sums(ROWS, lambda r: r[1], 3).items()}
    exp.update({("store", k): v for k, v in sums(STORE, lambda r: r[1], 3).items()})
    assert res["columns"] == ["source", "Category", "Units_sum"]
    assert {(r[0], r[1]): r[2] for r in res["rows"]} == pytest.approx(exp)
    assert res["chart"] == {"type": "bar", "x": "Category", "y": ["Units_sum"], "series": "source"}
    assert [m["value"] for m in res["metrics"]] == [len(ROWS), len(STORE)]


def test_compare_over_time_is_a_line(data):
    res = engine.compare(two(data), {"group_by": [{"column": "date", "bucket": "month"}],
                                     "aggregations": [{"column": "Revenue", "agg": "sum"}]})
    assert res["chart"]["type"] == "line" and res["chart"]["x"] == "Date" and res["chart"]["series"] == "source"
    exp = sums(STORE, lambda r: r[0].strftime("%Y-%m"), 4)
    got = {r[1]: r[2] for r in res["rows"] if r[0] == "store"}
    assert got == pytest.approx(dict(exp))


def test_compare_without_group_is_one_bar_per_file(data):
    res = engine.compare(two(data), {"aggregations": [{"column": "Units", "agg": "sum"}]})
    assert res["rows"] == [["orders", sum(r[3] for r in ROWS)], ["store", sum(r[3] for r in STORE)]]
    assert res["chart"]["type"] == "bar" and res["chart"]["x"] == "source"


def test_compare_unknown_value_in_all_files_gives_empty_not_error(data):
    res = engine.compare(two(data), {"filters": [{"column": "Region", "op": "eq", "value": "West"}],
                                     "group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}]})
    assert res["rows"] == [] and res["chart"]["type"] == "table" and res["notes"]


def test_compare_refusals(data):
    t, plan = two(data), {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}]}
    for bad_tables in ([t[0]], [{"name": f"t{i}", "df": data["orders"]} for i in range(config.MAX_COMPARE + 1)],
                       [t[0], {"name": "orders", "df": data["store"]}]):
        with pytest.raises(PlanError):
            engine.compare(bad_tables, plan)
    for bad_plan in ({**plan, "top_n": 3}, {"group_by": ["Category", "Region"], "aggregations": plan["aggregations"]},
                     {"group_by": ["Category"], "aggregations": [{"column": "Units", "agg": "sum"}, {"column": "*", "agg": "count"}]}):
        with pytest.raises(PlanError):
            engine.compare(t, bad_plan)
    with pytest.raises(PlanError) as e:
        engine.compare([t[0], {"name": "messy", "df": data["messy"]}], plan)
    assert "messy" in str(e.value)
    
def test_sort_accepts_unambiguous_source_column_and_count(data):
    agg = [{"column": "Units", "agg": "sum"}]
    res = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": agg,
                                          "sort": {"by": "Units", "desc": True}, "top_n": 2})
    assert len(res["rows"]) == 2 and res["rows"][0][1] >= res["rows"][1][1]
    res = engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": [{"column": "*", "agg": "count"}],
                                          "sort": {"by": "count"}})
    assert [r[1] for r in res["rows"]] == sorted((r[1] for r in res["rows"]), reverse=True)


def test_sort_alias_is_refused_when_ambiguous_or_unknown(data):
    two = [{"column": "Units", "agg": "sum"}, {"column": "Units", "agg": "mean"}]
    with pytest.raises(PlanError):
        engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": two, "sort": {"by": "Units"}})
    with pytest.raises(PlanError):
        engine.execute(data["orders"], {"group_by": ["Category"], "aggregations": two[:1], "sort": {"by": "Revenue"}})