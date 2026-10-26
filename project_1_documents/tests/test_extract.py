import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docqa import extract as ex  # noqa: E402
from docqa.registry import FileRecord  # noqa: E402

FIX = HERE.parent / "fixtures"
Q = json.loads((FIX / "questions.json").read_text(encoding="utf-8"))
TRUTH = Q["truth"]


def rec(name, tmp, kind=None):
    src = FIX / name
    dst = tmp / name
    shutil.copyfile(src, dst)
    import hashlib
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()
    k = kind or {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv", "png": "png"}[name.rsplit(".", 1)[1]]
    return FileRecord("repo:" + name, name, k, dst.stat().st_size, 0, sha, 1, dst, "repository")


@pytest.fixture(scope="module", autouse=True)
def fixtures_exist():
    assert (FIX / "sales_report.pdf").is_file(), "run make_fixtures.py first"


def test_text_pdf_needs_no_ocr(tmp_path):
    d = ex.extract_pdf(FIX / "sales_report.pdf")
    assert d["page_count"] == 2
    assert all(p["method"] == "text" for p in d["pages"])
    p2 = d["pages"][1]["text"]
    assert "Gizmo" in p2 and "1800" in p2 and "finance team" in p2


def test_scanned_pdf_uses_ocr_and_finds_total():
    d = ex.extract_pdf(FIX / "scanned_invoice.pdf")
    assert d["pages"][0]["method"] == "ocr"
    assert "1840" in d["pages"][0]["text"]


def test_image_only_multipage_pdf_ocr_each_page():
    d = ex.extract_pdf(FIX / "mixed_report.pdf")
    assert [p["method"] for p in d["pages"]] == ["ocr", "ocr"]
    assert "ZX-4471" in d["pages"][1]["text"].replace(" ", "")
    assert d["pages"][1]["page"] == 2


def test_ocr_off_reports_warning_and_does_not_run_ocr():
    d = ex.extract_pdf(FIX / "scanned_invoice.pdf", ocr=False)
    assert d["pages"][0]["method"] == "text" and d["pages"][0]["text"] == ""
    assert d["warnings"]


def test_true_mixed_pdf_ocr_only_on_scanned_page(tmp_path):
    """Page 1 has a text layer (from sales_report.pdf), page 2 is image-only (from the invoice)."""
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    w.add_page(PdfReader(str(FIX / "sales_report.pdf")).pages[0])
    w.add_page(PdfReader(str(FIX / "scanned_invoice.pdf")).pages[0])
    out = tmp_path / "true_mixed.pdf"
    with open(out, "wb") as f:
        w.write(f)
    d = ex.extract_pdf(out)
    assert [p["method"] for p in d["pages"]] == ["text", "ocr"]
    assert "2000" in d["pages"][0]["text"] and "1840" in d["pages"][1]["text"]


def test_damaged_pdf_gives_clean_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not really a pdf")
    with pytest.raises(ex.ExtractError):
        ex.extract_pdf(bad)


def test_xlsx_header_detected_below_title_row(tmp_path):
    d = ex.extract_file(rec("sales_2025.xlsx", tmp_path), tmp_path / "cache")
    names = [s["sheet"] for s in d["sheets"]]
    assert names == ["Sales", "Targets"]
    sales = d["sheets"][0]
    assert sales["header_row"] == 3
    assert [c["name"] for c in sales["columns"]] == ["Month", "Region", "Product", "Units", "Revenue"]
    assert sales["row_count"] == 12 and sales["source_rows"][0] == 4 and sales["source_rows"][-1] == 15
    assert sum(r["Revenue"] for r in sales["rows"]) == TRUTH["total"]
    targets = d["sheets"][1]
    assert targets["header_row"] == 1 and targets["rows"][1] == {"Region": "South", "Annual target": 8000}


def test_csv_profile_and_numbers(tmp_path):
    d = ex.extract_file(rec("sales_2025.csv", tmp_path), tmp_path / "cache")
    t = d["sheets"][0]
    assert t["row_count"] == 12 and t["header_row"] == 1
    assert sum(r["Revenue"] for r in t["rows"]) == TRUTH["total"]
    assert sum(r["Revenue"] for r in t["rows"] if r["Region"] == "North") == TRUTH["north"]
    rev = next(c for c in t["columns"] if c["name"] == "Revenue")
    assert "int" in rev["dtype"] and rev["nulls"] == 0


def test_missing_values_and_empty_sheet_profiled(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Item", "Qty"])
    ws.append(["a", 1])
    ws.append(["b", None])
    ws.append(["c", 3])
    wb.create_sheet("Empty")
    p = tmp_path / "m.xlsx"
    wb.save(p)
    d = ex.extract_xlsx(p)
    data = d["sheets"][0]
    qty = next(c for c in data["columns"] if c["name"] == "Qty")
    assert qty["nulls"] == 1 and data["row_count"] == 3
    assert d["sheets"][1]["empty"] is True


def test_duplicate_headers_are_made_unique():
    assert ex._dedupe(["a", "a", "", "a"]) == ["a", "a_2", "column", "a_3"]


def test_cache_hit_and_new_hash_after_change(tmp_path):
    r = rec("sales_2025.csv", tmp_path)
    cache = tmp_path / "cache"
    first = ex.extract_file(r, cache)
    second = ex.extract_file(r, cache)
    assert first["cached"] is False and second["cached"] is True
    assert (cache / f"{r.sha256}.json").is_file()
    changed = FileRecord(r.file_id, r.name, r.kind, r.size, 0, "f" * 64, 2, r.path, r.source)
    assert ex.extract_file(changed, cache)["cached"] is False


def test_cache_respects_ocr_setting(tmp_path):
    r = rec("scanned_invoice.pdf", tmp_path)
    cache = tmp_path / "cache"
    off = ex.extract_file(r, cache, ocr=False)
    on = ex.extract_file(r, cache, ocr=True)
    assert off["pages"][0]["text"] == "" and "1840" in on["pages"][0]["text"] and on["cached"] is False