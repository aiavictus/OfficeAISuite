import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docqa import calc, extract as ex, retrieve  # noqa: E402
from docqa.registry import FileRecord  # noqa: E402

FIX = HERE.parent / "fixtures"
Q = json.loads((FIX / "questions.json").read_text(encoding="utf-8"))
TRUTH = Q["truth"]
QS = {q["id"]: q for q in Q["questions"]}


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("w")
    import hashlib
    out = {}
    for name in ("sales_2025.xlsx", "sales_2025.csv", "employees.csv", "sales_report.pdf",
                 "scanned_invoice.pdf", "mixed_report.pdf"):
        dst = tmp / name
        shutil.copyfile(FIX / name, dst)
        kind = name.rsplit(".", 1)[1]
        sha = hashlib.sha256(dst.read_bytes()).hexdigest()
        r = FileRecord("repo:" + name, name, kind, dst.stat().st_size, 0, sha, 1, dst, "repository")
        out[name] = ex.extract_file(r, tmp / "cache")
    return out


def sel(data, q):
    return [data[f] for f in QS[q]["files"]]


# ---- calculations match the known truth ----
@pytest.mark.parametrize("qid", ["q1", "q2", "q3", "q4", "q5"])
def test_calculations_match_expected(data, qid):
    res = calc.try_calculate(QS[qid]["question"], sel(data, qid))
    assert res is not None, QS[qid]["question"]
    assert res.value == pytest.approx(QS[qid]["expected"])


def test_filter_and_citation_details(data):
    res = calc.try_calculate(QS["q2"]["question"], sel(data, "q2"))
    assert res.filters == {"Region": "North"} and res.rows_used == 6 and "sales_2025.csv" in res.cite
    res = calc.try_calculate(QS["q1"]["question"], sel(data, "q1"))
    assert res.cite == "sales_2025.xlsx / Sales, rows 4-15"
    res = calc.try_calculate(QS["q5"]["question"], sel(data, "q5"))
    assert "Targets" in res.cite and res.filters == {"Region": "South"}


def test_unanswerable_or_unsupported_returns_none(data):
    assert calc.try_calculate(QS["u2"]["question"], sel(data, "u2")) is None       # July: no rows
    assert calc.try_calculate("Who prepared the report?", [data["sales_report.pdf"]]) is None
    assert calc.try_calculate("What is the total revenue?", []) is None


def test_ambiguous_across_tables_is_not_guessed(data):
    both = [data["sales_2025.csv"], data["sales_2025.xlsx"]]
    assert calc.try_calculate("What is the total revenue?", both) is None


def test_two_values_of_one_column_is_not_a_simple_filter(data):
    assert calc.try_calculate("What is the total revenue for North and South?", [data["sales_2025.csv"]]) is None


# ---- retrieval ----
def test_pdf_fact_retrieved_with_page_citation(data):
    chunks = retrieve.build_chunks([data["sales_report.pdf"]])
    top, score = retrieve.search(chunks, "Who prepared the report?")[0]
    assert top.cite == "sales_report.pdf, page 2" and "finance team" in top.text
    top, _ = retrieve.search(chunks, QS["q6"]["question"])[0]
    assert top.cite == "sales_report.pdf, page 2" and "1800" in top.text


def test_ocr_facts_retrievable(data):
    top, _ = retrieve.search(retrieve.build_chunks([data["scanned_invoice.pdf"]]), QS["q8"]["question"])[0]
    assert top.cite == "scanned_invoice.pdf, page 1" and "1840" in top.text
    top, _ = retrieve.search(retrieve.build_chunks([data["mixed_report.pdf"]]), QS["q9"]["question"])[0]
    assert top.cite == "mixed_report.pdf, page 2"


def test_spreadsheet_chunks_have_row_range_citations(data):
    chunks = retrieve.build_chunks([data["sales_2025.xlsx"]])
    cites = [c.cite for c in chunks]
    assert "sales_2025.xlsx / Sales, rows 4-15" in cites
    assert any(c.kind == "schema" for c in chunks)
    assert any("Targets" in c for c in cites)


# ---- selected files only ----
def test_unselected_file_content_never_appears(data):
    """u1: the answer (Asha's salary) lives only in employees.csv, which is NOT selected."""
    chunks = retrieve.build_chunks(sel(data, "u1"))
    assert all(c.file == "sales_2025.csv" for c in chunks)
    assert retrieve.search(chunks, QS["u1"]["question"]) == []
    assert calc.try_calculate(QS["u1"]["question"], sel(data, "u1")) is None


def test_selecting_the_file_makes_it_available(data):
    chunks = retrieve.build_chunks([data["employees.csv"]])
    hits = retrieve.search(chunks, "What is Asha's salary?")
    assert hits and "Asha" in hits[0][0].text


def test_no_selection_or_no_match_gives_nothing(data):
    assert retrieve.search([], "anything") == []
    assert retrieve.search(retrieve.build_chunks([data["sales_2025.csv"]]), "???") == []