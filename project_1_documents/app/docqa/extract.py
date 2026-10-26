"""Deterministic extraction: PDF text (+ OCR only where needed), spreadsheet profiling, images.

Results are cached as data/extracted/<sha256>.json. A changed file has a new sha256,
so stale content is never reused.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from shared.paths import TESSERACT_DIR

OCR_MIN_CHARS = 20          # fewer characters than this on a page => page needs OCR
OCR_DPI_SCALE = 200 / 72    # render scale for OCR
MAX_PDF_PAGES = 300
MAX_OCR_PAGES = 40
MAX_SHEET_ROWS = 200_000
HEADER_SCAN_ROWS = 15
TESSERACT = TESSERACT_DIR / "tesseract.exe"


class ExtractError(RuntimeError):
    """Message is safe to show to the user."""


# ---------------------------------------------------------------- OCR
def ocr_image(pil_image, timeout=120):
    """Run the bundled Tesseract on a PIL image. Returns text."""
    if not TESSERACT.is_file():
        raise ExtractError("OCR engine (tesseract.exe) not found in the suite.")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "page.png"
        pil_image.convert("L").save(src)
        env = dict(os.environ, TESSDATA_PREFIX=str(TESSERACT_DIR / "tessdata"))
        try:
            r = subprocess.run([str(TESSERACT), str(src), "stdout", "-l", "eng", "--psm", "6"],
                               capture_output=True, timeout=timeout, env=env, cwd=td)
        except subprocess.TimeoutExpired as exc:
            raise ExtractError("OCR timed out on a page.") from exc
        if r.returncode != 0:
            raise ExtractError("OCR failed: " + r.stderr.decode("utf-8", "replace")[:200])
        return r.stdout.decode("utf-8", "replace").strip()


# ---------------------------------------------------------------- PDF
def extract_pdf(path, ocr=True):
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ExtractError("The PDF could not be opened (damaged or password protected).") from exc
    try:
        n = len(pdf)
        if n > MAX_PDF_PAGES:
            raise ExtractError(f"PDF has {n} pages; the limit is {MAX_PDF_PAGES}.")
        pages, need_ocr = [], []
        for i in range(n):
            tp = pdf[i].get_textpage()
            try:
                text = tp.get_text_bounded().strip()
            finally:
                tp.close()
            pages.append({"page": i + 1, "text": text, "method": "text"})
            if len(text) < OCR_MIN_CHARS:
                need_ocr.append(i)
        warnings = []
        if need_ocr:
            if not ocr:
                warnings.append(f"{len(need_ocr)} page(s) have no text layer and OCR is off.")
            elif len(need_ocr) > MAX_OCR_PAGES:
                warnings.append(f"{len(need_ocr)} pages need OCR; only the first {MAX_OCR_PAGES} were read.")
                need_ocr = need_ocr[:MAX_OCR_PAGES]
            if ocr:
                for i in need_ocr:
                    img = pdf[i].render(scale=OCR_DPI_SCALE).to_pil()
                    pages[i]["text"] = ocr_image(img)
                    pages[i]["method"] = "ocr"
        return {"kind": "pdf", "page_count": n, "pages": pages, "warnings": warnings}
    finally:
        pdf.close()


# ---------------------------------------------------------------- images
def extract_image(path, ocr=True):
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.load()
            text = ocr_image(im) if ocr else ""
    except ExtractError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractError("The image could not be read.") from exc
    return {"kind": "image", "page_count": 1,
            "pages": [{"page": 1, "text": text, "method": "ocr"}], "warnings": []}


# ---------------------------------------------------------------- spreadsheets
def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def detect_header_row(raw):
    """Index of the header row in a header-less frame: the first row, within the first
    HEADER_SCAN_ROWS, with the most non-empty cells that are mostly text and followed by data."""
    best, best_score = 0, -1
    for i in range(min(HEADER_SCAN_ROWS, len(raw))):
        row = raw.iloc[i]
        filled = [v for v in row if pd.notna(v) and str(v).strip() != ""]
        if len(filled) < 2:
            continue
        text_share = sum(1 for v in filled if not _is_number(v)) / len(filled)
        if text_share < 0.6 or i + 1 >= len(raw):
            continue
        nxt = raw.iloc[i + 1]
        if sum(1 for v in nxt if pd.notna(v)) < max(1, len(filled) // 2):
            continue
        score = len(filled)
        if score > best_score:
            best, best_score = i, score
    return best


def _dedupe(names):
    seen, out = {}, []
    for n in names:
        n = str(n).strip() or "column"
        k = seen.get(n, 0)
        seen[n] = k + 1
        out.append(n if k == 0 else f"{n}_{k + 1}")
    return out


def _to_numeric_if_possible(s):
    """Convert an object column to numbers only if every non-empty value converts."""
    if s.dtype != object:
        return s
    try:
        return pd.to_numeric(s)
    except (ValueError, TypeError):
        return s


def profile_frame(raw, sheet_name):
    """Turn a header-less frame into a profiled table with source-row mapping."""
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return {"sheet": sheet_name, "empty": True, "columns": [], "rows": [], "row_count": 0}
    if len(raw) > MAX_SHEET_ROWS:
        raise ExtractError(f"Sheet '{sheet_name}' has more than {MAX_SHEET_ROWS} rows.")
    h = detect_header_row(raw)
    header_src_row = int(raw.index[h]) + 1                    # 1-based row number in the source
    cols = _dedupe(raw.iloc[h].tolist())
    body = raw.iloc[h + 1:].copy()
    body.columns = cols
    src_rows = [int(i) + 1 for i in body.index]
    body = body.reset_index(drop=True)
    body = body.apply(_to_numeric_if_possible)
    profile = []
    for c in cols:
        s = body[c]
        profile.append({"name": c, "dtype": str(s.dtype), "non_null": int(s.notna().sum()),
                        "nulls": int(s.isna().sum()),
                        "sample": [None if pd.isna(v) else str(v) for v in s.dropna().head(3)]})
    return {"sheet": sheet_name, "empty": False, "header_row": header_src_row,
            "row_count": int(len(body)), "columns": profile,
            "rows": json.loads(body.to_json(orient="records", date_format="iso")),
            "source_rows": src_rows}


def extract_xlsx(path):
    try:
        xl = pd.ExcelFile(str(path), engine="openpyxl")
    except Exception as exc:  # noqa: BLE001
        raise ExtractError("The Excel file could not be opened.") from exc
    sheets = []
    for name in xl.sheet_names:
        raw = xl.parse(name, header=None)
        sheets.append(profile_frame(raw, name))
    return {"kind": "xlsx", "sheets": sheets, "warnings": []}


def extract_csv(path):
    raw = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            raw = pd.read_csv(str(path), header=None, dtype=object, encoding=enc,
                              sep=None, engine="python", skip_blank_lines=False)
            break
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise ExtractError("The CSV file could not be parsed.") from exc
    if raw is None:
        raise ExtractError("The CSV file encoding is not supported (use UTF-8).")
    return {"kind": "csv", "sheets": [profile_frame(raw, "csv")], "warnings": []}


# ---------------------------------------------------------------- entry point with cache
def extract_file(record, cache_dir, ocr=True):
    """Extract (or load from cache) the content of one FileRecord."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{record.sha256}.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("ocr") == bool(ocr):
                data["cached"] = True
                return data
        except (OSError, ValueError):
            pass
    k = record.kind
    if k == "pdf":
        data = extract_pdf(record.path, ocr)
    elif k in ("png", "jpg"):
        data = extract_image(record.path, ocr)
    elif k == "xlsx":
        data = extract_xlsx(record.path)
    elif k == "csv":
        data = extract_csv(record.path)
    else:
        raise ExtractError(f"Unsupported file kind: {k}")
    data.update({"file": record.name, "sha256": record.sha256, "ocr": bool(ocr), "cached": False})
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache)
    return data