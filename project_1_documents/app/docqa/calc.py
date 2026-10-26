"""Deterministic spreadsheet calculations. No model involved.

Returns None whenever the question cannot be mapped confidently, so the caller falls
back to the LLM path instead of guessing.
"""
import re
from dataclasses import dataclass

import pandas as pd

AGGS = (("average", "mean"), ("mean", "mean"), ("avg", "mean"), ("total", "sum"), ("sum", "sum"),
        ("how many", "count"), ("count", "count"), ("number of", "count"),
        ("highest", "max"), ("maximum", "max"), ("max", "max"),
        ("lowest", "min"), ("minimum", "min"), ("min", "min"))
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december",
          "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
QUESTION_WORDS = {"what", "which", "who", "how", "when", "where", "why", "is", "are", "do", "does",
                  "show", "give", "tell", "calculate", "find"}
                  


@dataclass
class CalcResult:
    value: float
    operation: str
    column: str
    filters: dict
    rows_used: int
    cite: str
    explanation: str


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _pick_agg(question):
    q = " " + question.lower() + " "
    for word, agg in AGGS:
        if re.search(r"\b" + re.escape(word) + r"\b", q):
            return word, agg
    return None, None


def _frame(sheet):
    return pd.DataFrame(sheet["rows"], columns=[c["name"] for c in sheet["columns"]])


def _numeric_columns(sheet, df):
    return [c["name"] for c in sheet["columns"]
            if pd.api.types.is_numeric_dtype(df[c["name"]]) and df[c["name"]].notna().any()]


def _find_column(question, names):
    """The column whose whole name appears in the question (longest first). Ambiguity => None."""
    qn = " " + _norm(question) + " "
    hits = [n for n in names if f" {_norm(n)} " in qn]
    hits.sort(key=lambda n: -len(_norm(n)))
    if len(hits) >= 2 and len(_norm(hits[0])) == len(_norm(hits[1])):
        return None
    return hits[0] if hits else None


def _filters(question, sheet, df, skip_col):
    """Equality filters: a text-column value that appears verbatim in the question."""
    qn = " " + _norm(question) + " "
    found = {}
    for c in sheet["columns"]:
        name = c["name"]
        if name == skip_col or pd.api.types.is_numeric_dtype(df[name]):
            continue
        for v in df[name].dropna().astype(str).unique():
            if _norm(v) and f" {_norm(v)} " in qn:
                found.setdefault(name, []).append(v)
    if any(len(v) > 1 for v in found.values()):
        return None                      # two values of one column: not a simple equality filter
    return {k: v[0] for k, v in found.items()}


def _has_unknown_specifier(question, sheet, df, col, filt, file_name=""):
    """True if the question names a value (capitalised word, number or month) that is not in the table.

    Such a question must not be answered by aggregating the whole table.
    """
    known = set()
    for c in sheet["columns"]:
        known.update(_norm(c["name"]).split())
    for v in df.astype(str).to_numpy().ravel():
        known.update(_norm(v).split())
    known.update(_norm(col).split())
    known.update(_norm(sheet.get("sheet", "")).split())
    known.update(_norm(file_name).split())
    for v in filt.values():
        known.update(_norm(v).split())
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", question)
    for i, w in enumerate(words):
        parts = _norm(w).split()
        if not parts or all(p in known for p in parts) or w.lower() in QUESTION_WORDS:
            continue
        lw = w.lower().strip("'-")
        looks_specific = (any(ch.isdigit() for ch in w) or lw in MONTHS
                          or (w[0].isupper() and i > 0))
        if looks_specific and lw != "s":
            return True
    return False


def try_calculate(question, extracted_list):
    """Answer with pandas if possible. Considers every non-empty table in the selected files.

    Two modes: an aggregation (sum, average, count, min, max) or a single-value lookup
    (no aggregation word, a numeric column named, and filters that leave exactly one row).
    """
    word, agg = _pick_agg(question)
    lookup = agg is None
    if lookup:
        agg = "lookup"
    candidates = []
    for d in extracted_list:
        for s in d.get("sheets", []):
            if s.get("empty") or not s["rows"]:
                continue
            candidates.append((d, s))
    results = []
    for d, s in candidates:
        df = _frame(s)
        nums = _numeric_columns(s, df)
        col = _find_column(question, nums)
        if col is None and agg == "count":
            col = "*"
        if col is None:
            continue
        filt = _filters(question, s, df, col)
        if filt is None:
            continue
        if _has_unknown_specifier(question, s, df, col, filt, d.get("file", "")):
            continue                     # names a value that is not in this table: do not aggregate everything
        sub = df
        for k, v in filt.items():
            sub = sub[sub[k].astype(str) == v]
        if len(sub) == 0:
            continue
        if lookup and len(sub) != 1:
            continue                     # filters do not pin down a single row: do not guess
        if col == "*":
            value = float(len(sub))
        else:
            series = sub[col].dropna()
            if series.empty:
                continue
            value = float(series.iloc[0]) if lookup else float(getattr(series, agg)())
        src = s["source_rows"]
        idx = list(sub.index)
        where = f"{d['file']} / {s['sheet']}" if d["kind"] == "xlsx" else d["file"]
        cite = f"{where}, rows {src[min(idx)]}-{src[max(idx)]}"
        flt = ", ".join(f"{k}={v}" for k, v in filt.items()) or "none"
        results.append(CalcResult(value, agg, col, filt, int(len(sub)), cite,
                                  f"{agg} of {col} over {len(sub)} row(s), filters: {flt}"))
    if len(results) != 1:
        return None                      # nothing matched, or several tables matched: do not guess
    return results[0]