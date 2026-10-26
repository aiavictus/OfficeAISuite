"""Rule-based suggestions: ready-made, always-valid plans from column kinds. No model involved.
The English name hints below (price, rate, id ...) only choose between average and total, or skip id columns."""
import re

import pandas as pd

from .plan import PlanError, validate_plan
from .profile import column_kind

MEAN_HINT = re.compile(r"price|rate|avg|average|ratio|pct|percent|score|age|temp|margin", re.I)
ID_HINT = re.compile(r"(^|[_ ])id$|^index$|code$", re.I)


def _is_id(name, s):
    return bool(ID_HINT.search(name)) or (pd.api.types.is_integer_dtype(s) and s.notna().all()
                                          and s.nunique() == len(s) and len(s) > 20)


def _bucket_for(days):
    return "day" if days <= 62 else ("month" if days <= 1100 else "year")


def _fn(measure):
    return "mean" if MEAN_HINT.search(measure) else "sum"


def _title(measure):
    return ("Average " if _fn(measure) == "mean" else "Total ") + measure


def suggest(df, limit=10):
    kinds = {c: column_kind(df[c]) for c in df.columns}
    measures = [c for c in df.columns if kinds[c] == "numeric" and not _is_id(c, df[c])][:3]
    cats = [c for c in df.columns if kinds[c] == "categorical" and 2 <= df[c].nunique() <= 20][:3]
    dates = [c for c in df.columns if kinds[c] == "datetime"][:1]
    out = []

    def add(title, plan):
        try:
            validate_plan(plan, df)
        except PlanError:
            return
        out.append({"title": title, "plan": plan})

    agg = lambda m: {"column": m, "agg": _fn(m)}      # noqa: E731
    if dates and measures:
        s = df[dates[0]].dropna()
        if not s.empty:
            b = _bucket_for((s.max() - s.min()).days)
            add(f"{_title(measures[0])} by {b}", {"group_by": [{"column": dates[0], "bucket": b}],
                                                  "aggregations": [agg(measures[0])], "chart": "line"})
    for m in measures[:1]:
        for c in cats[:2]:
            add(f"{_title(m)} by {c}", {"group_by": [c], "aggregations": [agg(m)], "chart": "bar"})
    if len(measures) > 1 and cats:
        add(f"{_title(measures[1])} by {cats[0]}", {"group_by": [cats[0]], "aggregations": [agg(measures[1])], "chart": "bar"})
    if cats:
        add(f"Number of rows by {cats[0]}", {"group_by": [cats[0]], "aggregations": [{"column": "*", "agg": "count"}], "chart": "bar"})
    if len(measures) > 1:
        add(f"{measures[1]} against {measures[0]}", {"chart": "scatter", "x": measures[0], "y": measures[1]})
    if measures:
        add("Key totals", {"aggregations": [agg(m) for m in measures[:3]] + [{"column": "*", "agg": "count"}], "chart": "metric"})
    add("First rows of the data", {"chart": "table"})
    return out[:limit]


# ---------------------------------------------------------------- several files
def _cls(kind):
    return "label" if kind in ("categorical", "text") else kind


def shared_columns(tables):
    """Columns that appear (by name, ignoring case) in 2+ files with compatible kinds."""
    seen = {}
    for t in tables:
        for c in t["df"].columns:
            kind = column_kind(t["df"][c])
            if kind != "empty":
                seen.setdefault(c.lower(), []).append((t["name"], c, kind))
    out = []
    for items in seen.values():
        if len(items) >= 2 and len({_cls(k) for _, _, k in items}) == 1:
            kind = items[0][2]
            out.append({"column": items[0][1], "kind": kind, "files": [n for n, _, _ in items],
                        "role": {"numeric": "measure", "datetime": "date"}.get(kind, "dimension")})
    return out


def comparison_suggestions(tables, limit=8):
    """Plans that every compared file can run: only columns present in ALL files are used."""
    n = len(tables)
    full = [s for s in shared_columns(tables) if len(s["files"]) == n]
    measures = [s["column"] for s in full if s["role"] == "measure" and not ID_HINT.search(s["column"])][:2]
    dims = [s["column"] for s in full if s["role"] == "dimension"][:2]
    dates = [s["column"] for s in full if s["role"] == "date"][:1]
    cand = []
    if dates and measures:
        lo = min(t["df"][next(c for c in t["df"].columns if c.lower() == dates[0].lower())].min() for t in tables)
        hi = max(t["df"][next(c for c in t["df"].columns if c.lower() == dates[0].lower())].max() for t in tables)
        b = _bucket_for((hi - lo).days) if pd.notna(lo) and pd.notna(hi) else "month"
        cand.append((f"{_title(measures[0])} by {b}, per file", {"group_by": [{"column": dates[0], "bucket": b}],
                                                                 "aggregations": [{"column": measures[0], "agg": _fn(measures[0])}], "chart": "line"}))
    for m in measures[:1]:
        for d in dims:
            cand.append((f"{_title(m)} by {d}, per file", {"group_by": [d], "aggregations": [{"column": m, "agg": _fn(m)}], "chart": "bar"}))
    for d in dims[:1]:
        cand.append((f"Number of rows by {d}, per file", {"group_by": [d], "aggregations": [{"column": "*", "agg": "count"}], "chart": "bar"}))
    for m in measures:
        cand.append((f"{_title(m)}, one bar per file", {"aggregations": [{"column": m, "agg": _fn(m)}], "chart": "bar"}))
    out = []
    for title, plan in cand:
        try:
            for t in tables:
                validate_plan(plan, t["df"], strict_values=False)
        except PlanError:
            continue
        out.append({"title": title, "plan": plan})
    return out[:limit]