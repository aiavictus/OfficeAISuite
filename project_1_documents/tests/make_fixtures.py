"""Build deterministic Project 1 test fixtures.
Run from the suite root after `call ENV.bat`:
    python project_1_documents\\tests\\make_fixtures.py
Writes into project_1_documents\\tests\\fixtures\\ and prints a self-check.
"""
import csv
import json
import sys
from pathlib import Path

import openpyxl
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)

# Known data. Every expected answer is computed from this list, not typed by hand.
SALES = [  # month, region, product, units, revenue
    ("2025-01", "North", "Widget", 100, 2000), ("2025-01", "South", "Widget", 80, 1600),
    ("2025-02", "North", "Gadget", 50, 1500), ("2025-02", "South", "Gadget", 40, 1200),
    ("2025-03", "North", "Widget", 120, 2400), ("2025-03", "South", "Gizmo", 30, 900),
    ("2025-04", "North", "Gizmo", 60, 1800), ("2025-04", "South", "Widget", 90, 1800),
    ("2025-05", "North", "Gadget", 70, 2100), ("2025-05", "South", "Gizmo", 20, 600),
    ("2025-06", "North", "Widget", 110, 2200), ("2025-06", "South", "Gadget", 45, 1350),
]
TARGETS = [("North", 12000), ("South", 8000)]
QUARTERS = {"Q1": ["2025-01", "2025-02", "2025-03"], "Q2": ["2025-04", "2025-05", "2025-06"]}
TOTAL_REV = sum(r[4] for r in SALES)
NORTH_REV = sum(r[4] for r in SALES if r[1] == "North")
SOUTH_REV = sum(r[4] for r in SALES if r[1] == "South")
Q1_REV = sum(r[4] for r in SALES if r[0] in QUARTERS["Q1"])
Q2_REV = sum(r[4] for r in SALES if r[0] in QUARTERS["Q2"])
AVG_REV = TOTAL_REV / len(SALES)
WIDGET_UNITS = sum(r[3] for r in SALES if r[2] == "Widget")


def font(size):
    for name in ("arial.ttf", "calibri.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def page_image(lines, size=(1240, 1754)):
    """Render lines of text onto a white A4-like page (150 dpi)."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    y = 120
    for text, pt in lines:
        d.text((100, y), text, fill="black", font=font(pt))
        y += int(pt * 1.8)
    return img


def text_pdf(path, pages):
    """Real text PDF using pdfium-free approach: pypdf cannot draw, so write a minimal PDF by hand."""
    objs = []

    def add(b):
        objs.append(b)
        return len(objs)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    content_ids, page_ids = [], []
    for lines in pages:
        ops = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
        for ln in lines:
            safe = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content_ids.append(add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))
    pages_id_placeholder = len(objs) + len(pages) + 1
    for cid in content_ids:
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id_placeholder} 0 R /MediaBox [0 0 612 842] "
            f"/Contents {cid} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()))
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    assert pages_id == pages_id_placeholder
    cat_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {cat_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def table_lines(rows, title):
    lines = [title, ""]
    lines.append(f"{'Month':<9}{'Region':<8}{'Product':<9}{'Units':>6}{'Revenue':>9}")
    for r in rows:
        lines.append(f"{r[0]:<9}{r[1]:<8}{r[2]:<9}{r[3]:>6}{r[4]:>9}")
    return lines


def make_pdfs():
    text_pdf(OUT / "sales_report.pdf", [
        table_lines(SALES[:6], "ACME Sales Report 2025 - Page 1 (January to March)"),
        table_lines(SALES[6:], "ACME Sales Report 2025 - Page 2 (April to June)")
        + ["", "Prepared by the finance team."],
    ])
    inv = [("INVOICE 7731", 40), ("Customer: Orion Traders", 30), ("Item: Industrial pump", 30),
           ("Quantity: 4", 30), ("Total due: 1840 USD", 34)]
    img = page_image(inv)
    img.save(OUT / "scanned_invoice.pdf", "PDF", resolution=150.0)
    p1 = page_image([("Mixed report - text page", 36), ("Region North is the lead region.", 30)])
    p2 = page_image([("SCANNED PAGE", 40), ("Warehouse code: ZX-4471", 34), ("Stock count: 905", 34)])
    p1.save(OUT / "mixed_report.pdf", "PDF", resolution=150.0, save_all=True, append_images=[p2])


def make_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["ACME Sales 2025 (internal)"])
    ws.append([])
    ws.append(["Month", "Region", "Product", "Units", "Revenue"])
    for r in SALES:
        ws.append(list(r))
    ws2 = wb.create_sheet("Targets")
    ws2.append(["Region", "Annual target"])
    for t in TARGETS:
        ws2.append(list(t))
    wb.save(OUT / "sales_2025.xlsx")


def make_csvs():
    with open(OUT / "sales_2025.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Month", "Region", "Product", "Units", "Revenue"])
        w.writerows(SALES)
    with open(OUT / "employees.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Department", "Salary"])
        w.writerows([("Asha", "Finance", 5200), ("Ben", "Ops", 4800), ("Chen", "Finance", 6100)])


def make_questions():
    q = [
        {"id": "q1", "files": ["sales_2025.xlsx"], "question": "What is the total revenue on the Sales sheet?",
         "expected": TOTAL_REV, "type": "number", "cite": "sales_2025.xlsx / Sales"},
        {"id": "q2", "files": ["sales_2025.csv"], "question": "What is the total revenue for the North region?",
         "expected": NORTH_REV, "type": "number", "cite": "sales_2025.csv"},
        {"id": "q3", "files": ["sales_2025.csv"], "question": "What is the average revenue per row?",
         "expected": AVG_REV, "type": "number", "cite": "sales_2025.csv"},
        {"id": "q4", "files": ["sales_2025.csv"], "question": "How many Widget units were sold in total?",
         "expected": WIDGET_UNITS, "type": "number", "cite": "sales_2025.csv"},
        {"id": "q5", "files": ["sales_2025.xlsx"], "question": "What is the annual target for South?",
         "expected": 8000, "type": "number", "cite": "sales_2025.xlsx / Targets"},
        {"id": "q6", "files": ["sales_report.pdf"], "question": "What was the revenue for Gizmo in 2025-04 in the North region?",
         "expected": 1800, "type": "number", "cite": "sales_report.pdf page 2"},
        {"id": "q7", "files": ["sales_report.pdf"], "question": "Who prepared the report?",
         "expected": "finance team", "type": "text", "cite": "sales_report.pdf page 2"},
        {"id": "q8", "files": ["scanned_invoice.pdf"], "question": "What is the total due on the invoice?",
         "expected": "1840", "type": "text", "cite": "scanned_invoice.pdf page 1", "needs_ocr": True},
        {"id": "q9", "files": ["mixed_report.pdf"], "question": "What is the warehouse code?",
         "expected": "ZX-4471", "type": "text", "cite": "mixed_report.pdf page 2", "needs_ocr": True},
        {"id": "q10", "files": ["sales_2025.csv", "sales_report.pdf"], "question": "Do the CSV and PDF agree on Q1 revenue?",
         "expected": Q1_REV, "type": "number", "cite": "both files", "note": "cross-file"},
        {"id": "u1", "files": ["sales_2025.csv"], "question": "What is Asha's salary?",
         "expected": None, "type": "unanswerable", "note": "employees.csv is NOT selected: must say not found"},
        {"id": "u2", "files": ["sales_2025.csv"], "question": "What was the revenue in July 2025?",
         "expected": None, "type": "unanswerable", "note": "no July data exists"},
    ]
    (OUT / "questions.json").write_text(json.dumps(
        {"truth": {"total": TOTAL_REV, "north": NORTH_REV, "south": SOUTH_REV, "q1": Q1_REV, "q2": Q2_REV},
         "questions": q}, indent=2), encoding="utf-8")


def self_check():
    import pandas as pd
    import pypdfium2 as pdfium
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(("[PASS] " if cond else "[FAIL] ") + name + (f" - {detail}" if detail and not cond else ""))

    df = pd.read_excel(OUT / "sales_2025.xlsx", sheet_name="Sales", header=2)
    check("xlsx revenue total", int(df["Revenue"].sum()) == TOTAL_REV, str(df["Revenue"].sum()))
    c = pd.read_csv(OUT / "sales_2025.csv")
    check("csv revenue total", int(c["Revenue"].sum()) == TOTAL_REV)
    check("csv North total", int(c[c.Region == "North"]["Revenue"].sum()) == NORTH_REV)
    t = pdfium.PdfDocument(str(OUT / "sales_report.pdf"))
    check("sales_report.pdf has 2 pages", len(t) == 2)
    txt1 = t[0].get_textpage().get_text_range()
    txt2 = t[1].get_textpage().get_text_range()
    check("page 2 text has Gizmo 1800 row", "Gizmo" in txt2 and "1800" in txt2)
    check("page 2 text has 'finance team'", "finance team" in txt2)
    check("page 1 text has 2000", "2000" in txt1)
    s = pdfium.PdfDocument(str(OUT / "scanned_invoice.pdf"))
    check("scanned_invoice.pdf has no text layer", len(s[0].get_textpage().get_text_range().strip()) == 0)
    m = pdfium.PdfDocument(str(OUT / "mixed_report.pdf"))
    check("mixed_report.pdf has 2 pages", len(m) == 2)
    check("mixed_report.pdf has NO text layer on any page",
          all(len(m[i].get_textpage().get_text_range().strip()) == 0 for i in range(2)))
    return ok


def main():
    make_pdfs()
    make_xlsx()
    make_csvs()
    make_questions()
    print("Fixtures written to", OUT)
    return 0 if self_check() else 1


if __name__ == "__main__":
    sys.exit(main())