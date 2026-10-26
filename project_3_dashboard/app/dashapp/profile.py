"""Parsing and profiling of spreadsheet data. Deterministic pandas only; no model involved."""
import csv
import datetime as dt
import numbers
import re
import warnings

import pandas as pd

from . import config

HEADER_SCAN_ROWS = 15
CATEGORY_MAX_UNIQUE = 50
DATE_LIKE = re.compile(r"^\s*(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4})([ T]\d{1,2}:\d{2}(:\d{2})?)?\s*$")


class DataError(ValueError):
    """Message is safe to show to the user."""


def _is_number(v):
    return isinstance(v, numbers.Number) and not isinstance(v, bool) and v == v


def _name(v, i):
    if v is None or (isinstance(v, float) and v != v):
        return f"column_{i + 1}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()[:100] or f"column_{i + 1}"


def _dedupe(names):
    seen, out = {}, []
    for n in names:
        k = seen.get(n, 0)
        seen[n] = k + 1
        out.append(n if k == 0 else f"{n}_{k + 1}")
    return out


def detect_header_row(raw):
    """Index of the header row: the first row, within the first rows, with the most filled cells
    that are mostly text and are followed by a row with data. Defaults to row 0."""
    best, best_score = 0, -1
    for i in range(min(HEADER_SCAN_ROWS, len(raw) - 1)):
        filled = [v for v in raw.iloc[i] if pd.notna(v) and str(v).strip() != ""]
        if len(filled) < 2:
            continue
        if sum(1 for v in filled if not _is_number(v)) / len(filled) < 0.6:
            continue
        if sum(1 for v in raw.iloc[i + 1] if pd.notna(v)) < max(1, len(filled) // 2):
            continue
        if len(filled) > best_score:
            best, best_score = i, len(filled)
    return best


def _convert(s):
    """Object column -> numeric or datetime when (nearly) every value converts. Otherwise unchanged."""
    if s.dtype != object:
        return s
    nn = s.dropna()
    if nn.empty:
        return s
    try:
        return pd.to_numeric(s)
    except (ValueError, TypeError):
        pass
    sample = nn.head(200).tolist()
    if all(isinstance(v, (dt.datetime, dt.date)) for v in sample):
        return pd.to_datetime(s, errors="coerce")
    if all(isinstance(v, str) and DATE_LIKE.match(v) for v in sample):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(s, errors="coerce")
        if parsed.notna().sum() >= 0.9 * len(nn):
            return parsed
    return s


def build_table(raw):
    """Header-less frame -> (DataFrame, 1-based header row) or None when there is no data."""
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return None
    if len(raw) > config.MAX_ROWS + HEADER_SCAN_ROWS:
        raise DataError(f"A sheet has more than {config.MAX_ROWS} rows.")
    h = detect_header_row(raw)
    header_row = int(raw.index[h]) + 1
    names = _dedupe([_name(v, i) for i, v in enumerate(raw.iloc[h].tolist())])
    if len(names) > config.MAX_COLUMNS:
        raise DataError(f"A sheet has more than {config.MAX_COLUMNS} columns.")
    body = raw.iloc[h + 1:].copy()
    if body.empty:
        return None
    if len(body) > config.MAX_ROWS:
        raise DataError(f"A sheet has more than {config.MAX_ROWS} rows.")
    body.columns = names
    body = body.reset_index(drop=True).apply(_convert)
    return body, header_row


def _read_csv(path, limit):
    for enc in ("utf-8-sig", "cp1252"):
        for sep in (None, ","):
            try:
                return pd.read_csv(path, header=None, dtype=object, encoding=enc, sep=sep, engine="python",
                                   skip_blank_lines=False, nrows=limit, index_col=False,
                                   names=list(range(1000)))
            except UnicodeDecodeError:
                break                                   # try the next encoding
            except (pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error, ValueError):
                continue                                # try the next separator
    raise DataError("The CSV file could not be parsed.")


def _read_excel(path, limit):
    try:
        with pd.ExcelFile(str(path), engine="openpyxl") as xl:
            names = list(xl.sheet_names)
            if len(names) > config.MAX_SHEETS:
                raise DataError(f"The workbook has more than {config.MAX_SHEETS} sheets.")
            return [(n, xl.parse(n, header=None, nrows=limit)) for n in names]
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DataError("The Excel file could not be read.") from exc


def load_file(path, kind):
    """Return (tables, skipped_sheet_names). Each table: {"sheet", "df", "header_row"}."""
    limit = config.MAX_ROWS + HEADER_SCAN_ROWS + 2
    if kind == "csv":
        frames = [("data", _read_csv(path, limit))]
    elif kind == "xlsx":
        frames = _read_excel(path, limit)
    else:
        raise DataError("Unsupported file type.")
    tables, skipped = [], []
    for name, raw in frames:
        built = build_table(raw)
        if built is None:
            skipped.append(name)
            continue
        tables.append({"sheet": name, "df": built[0], "header_row": built[1]})
    if not tables:
        raise DataError("The file has no data rows.")
    return tables, skipped


# ---------------------------------------------------------------- profiling
def _num(x):
    if x is None or (isinstance(x, float) and x != x) or pd.isna(x):
        return None
    return int(x) if isinstance(x, numbers.Integral) else round(float(x), 6)


def _mixed(s):
    if s.dtype != object:
        return False
    kinds = {"num" if _is_number(v) else "str" if isinstance(v, str) else "other" for v in s.dropna().head(1000)}
    return {"num", "str"} <= kinds


def column_kind(s):
    if s.notna().sum() == 0:
        return "empty"
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_numeric_dtype(s):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    nn = s.dropna()
    u = nn.astype(str).nunique()
    return "categorical" if u <= CATEGORY_MAX_UNIQUE and u <= max(10, len(nn) * 0.5) else "text"


def profile_df(df):
    n = len(df)
    cols = []
    quality = {"duplicate_rows": int(df.duplicated().sum()), "empty_columns": [], "constant_columns": [],
               "high_null_columns": [], "mixed_type_columns": []}
    for name in df.columns:
        s = df[name]
        kind = column_kind(s)
        non_null = int(s.notna().sum())
        nulls = n - non_null
        info = {"name": name, "kind": kind, "dtype": str(s.dtype), "non_null": non_null, "nulls": nulls,
                "null_pct": round(100 * nulls / n, 1) if n else 0.0, "unique": int(s.nunique(dropna=True))}
        if kind == "numeric":
            info.update(min=_num(s.min()), max=_num(s.max()), mean=_num(s.mean()), sum=_num(s.sum()))
        elif kind == "datetime":
            info.update(min=s.min().isoformat(), max=s.max().isoformat())
        elif kind == "categorical":
            info["top"] = [{"value": str(k), "count": int(v)} for k, v in s.value_counts().head(5).items()]
        cols.append(info)
        if kind == "empty":
            quality["empty_columns"].append(name)
        else:
            if info["unique"] == 1:
                quality["constant_columns"].append(name)
            if info["null_pct"] >= 50:
                quality["high_null_columns"].append(name)
        if _mixed(s):
            quality["mixed_type_columns"].append(name)
    return {"rows": n, "columns": cols, "quality": quality}