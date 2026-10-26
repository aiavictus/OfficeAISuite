import json
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

import openpyxl
import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp import config  # noqa: E402
from dashapp.profile import DataError, load_file, profile_df  # noqa: E402


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return fx.build_all(tmp_path_factory.mktemp("fx"))


def load(path, kind):
    tables, skipped = load_file(path, kind)
    return {t["sheet"]: t for t in tables}, skipped


def col(profile, name):
    return next(c for c in profile["columns"] if c["name"] == name)


def test_excel_sheets_title_row_and_skipped_sheet(files):
    t, skipped = load(files["orders"], "xlsx")
    assert list(t) == ["Orders", "Notes"] and skipped == ["Blank"]
    assert t["Orders"]["header_row"] == 3 and len(t["Orders"]["df"]) == len(fx.orders_rows())
    assert list(t["Orders"]["df"].columns) == fx.HEADER
    assert t["Notes"]["header_row"] == 1 and len(t["Notes"]["df"]) == 2


def test_column_kinds(files):
    p = profile_df(load(files["orders"], "xlsx")[0]["Orders"]["df"])
    assert {c["name"]: c["kind"] for c in p["columns"]} == {
        "Date": "datetime", "Category": "categorical", "Region": "categorical",
        "Units": "numeric", "Revenue": "numeric"}


def test_numeric_profile_matches_independent_calculation(files):
    rows = fx.orders_rows()
    rev = [r[4] for r in rows if r[4] is not None]
    units = [r[3] for r in rows]
    p = profile_df(load(files["orders"], "xlsx")[0]["Orders"]["df"])
    r, u = col(p, "Revenue"), col(p, "Units")
    assert r["nulls"] == len(rows) - len(rev) and r["non_null"] == len(rev)
    assert r["min"] == pytest.approx(min(rev)) and r["max"] == pytest.approx(max(rev))
    assert r["mean"] == pytest.approx(sum(rev) / len(rev)) and r["sum"] == pytest.approx(sum(rev))
    assert u["sum"] == sum(units) and u["nulls"] == 0 and p["rows"] == len(rows)


def test_categorical_nulls_and_top_values(files):
    rows = fx.orders_rows()
    p = profile_df(load(files["orders"], "xlsx")[0]["Orders"]["df"])
    assert {t["value"]: t["count"] for t in col(p, "Category")["top"]} == dict(Counter(r[1] for r in rows))
    assert col(p, "Region")["nulls"] == sum(1 for r in rows if r[2] is None)


def test_date_range(files):
    rows = fx.orders_rows()
    d = col(profile_df(load(files["orders"], "xlsx")[0]["Orders"]["df"]), "Date")
    assert d["min"].startswith(fx.START.date().isoformat())
    assert d["max"].startswith((fx.START + timedelta(days=3 * (len(rows) - 1))).date().isoformat())


def test_csv_iso_date_strings_become_dates(files):
    rows = fx.store_b_rows()
    t, _ = load(files["store_b"], "csv")
    p = profile_df(t["data"]["df"])
    assert col(p, "Date")["kind"] == "datetime" and p["rows"] == 12 and t["data"]["header_row"] == 1
    assert col(p, "Revenue")["nulls"] == sum(1 for r in rows if r[4] is None)


def test_data_quality_report(files):
    rows = fx.MESSY_ROWS
    p = profile_df(load(files["messy"], "csv")[0]["data"]["df"])
    q = p["quality"]
    assert p["rows"] == len(rows)
    assert q["duplicate_rows"] == len(rows) - len(set(rows))
    assert q["constant_columns"] == ["country"] and q["empty_columns"] == ["notes"]
    nulls = sum(1 for r in rows if r[3] is None)
    assert col(p, "amount")["nulls"] == nulls and col(p, "amount")["null_pct"] == round(100 * nulls / len(rows), 1)
    assert col(p, "status")["nulls"] == 1


def test_mixed_type_excel_column_flagged(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "T"
    ws.append(["ref", "n"])
    for a, b in [("A1", 1), (7, 2), ("B2", 3), (9, 4)]:
        ws.append([a, b])
    p = tmp_path / "m.xlsx"
    wb.save(p)
    q = profile_df(load(p, "xlsx")[0]["T"]["df"])["quality"]
    assert q["mixed_type_columns"] == ["ref"]


def test_ragged_csv_loads(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("a,b\n1,2\n3,4,5\n", encoding="utf-8")
    df = load(p, "csv")[0]["data"]["df"]
    assert list(df.columns) == ["a", "b", "column_3"] and len(df) == 2


def test_cp1252_csv_loads(tmp_path):
    p = tmp_path / "c.csv"
    p.write_bytes("name,city\nJos\xe9,Z\xfcrich\nAna,Oslo\n".encode("cp1252"))
    assert load(p, "csv")[0]["data"]["df"]["name"].tolist() == ["Jos\xe9", "Ana"]


@pytest.mark.parametrize("content", ["", "a,b\n", "\n\n\n"])
def test_files_without_data_are_rejected(tmp_path, content):
    p = tmp_path / "e.csv"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(DataError):
        load_file(p, "csv")


def test_limits_are_enforced(files, monkeypatch):
    monkeypatch.setattr(config, "MAX_ROWS", 10)
    with pytest.raises(DataError):
        load_file(files["orders"], "xlsx")
    monkeypatch.undo()
    monkeypatch.setattr(config, "MAX_SHEETS", 1)
    with pytest.raises(DataError):
        load_file(files["orders"], "xlsx")
    monkeypatch.undo()
    monkeypatch.setattr(config, "MAX_COLUMNS", 3)
    with pytest.raises(DataError):
        load_file(files["store_b"], "csv")


def test_profile_is_json_safe(files):
    for name, kind in (("orders", "xlsx"), ("store_b", "csv"), ("messy", "csv")):
        for t in load(files[name], kind)[0].values():
            json.dumps(profile_df(t["df"]))