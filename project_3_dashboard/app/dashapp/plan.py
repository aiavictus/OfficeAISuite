"""Analysis plans: a strict schema validated against the REAL table.

A plan is untrusted JSON (typed by a user or proposed by a model). Nothing here executes code:
columns, operations, aggregations, buckets and filter values are checked against allowlists and the
actual data. Unknown filter values are rejected, never answered with the whole table.
"""
import math
import re
from dataclasses import dataclass, field

import pandas as pd

from .profile import column_kind

AGGS = ("sum", "mean", "count", "min", "max", "nunique")
BUCKETS = ("day", "month", "quarter", "year")
CHARTS = ("auto", "bar", "line", "pie", "scatter", "table", "metric")
ALLOWED_KEYS = {"filters", "group_by", "aggregations", "sort", "top_n", "chart", "x", "y", "columns"}
OPS_BY_KIND = {
    "numeric": {"eq", "ne", "in", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"},
    "datetime": {"gt", "gte", "lt", "lte", "between", "is_null", "not_null"},
    "categorical": {"eq", "ne", "in", "is_null", "not_null"},
    "text": {"eq", "ne", "in", "is_null", "not_null"},
    "boolean": {"eq", "ne", "in", "is_null", "not_null"},
    "empty": set(),
}
FILTER_OPS = set().union(*OPS_BY_KIND.values())
MAX_FILTERS, MAX_GROUPS, MAX_AGGS, MAX_IN, MAX_TOP_N, MAX_TABLE_COLUMNS = 8, 2, 4, 50, 100, 20
MAX_NUMERIC_GROUP_VALUES = 50
ALIAS = re.compile(r"^[A-Za-z0-9 _()\-]{1,40}$")


class PlanError(ValueError):
    """The plan was refused. The message is safe to show to the user."""


@dataclass(frozen=True)
class Filter:
    column: str
    op: str
    value: object
    kind: str


@dataclass(frozen=True)
class Group:
    column: str
    bucket: str | None
    kind: str


@dataclass(frozen=True)
class Agg:
    column: str          # "*" means all rows
    fn: str
    out: str             # output column name


@dataclass
class Plan:
    mode: str            # aggregate | table | scatter
    filters: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    aggs: list = field(default_factory=list)
    sort_by: str | None = None
    desc: bool = True
    top_n: int | None = None
    chart: str = "auto"
    x: str | None = None
    y: str | None = None
    columns: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _col(df, name, what):
    if not isinstance(name, str) or not name.strip():
        raise PlanError(f"{what}: a column name is required.")
    found = {c.lower(): c for c in df.columns}.get(name.strip().lower())
    if found is None:
        raise PlanError(f"Unknown column: {name.strip()[:60]}. Available: {', '.join(list(df.columns)[:20])}")
    return found


def _scalar(kind, v, col):
    if kind == "numeric":
        if isinstance(v, bool):
            raise PlanError(f"{col} needs a number.")
        try:
            x = float(v.replace(",", "")) if isinstance(v, str) else float(v)
        except (TypeError, ValueError, AttributeError):
            raise PlanError(f"{col} needs a number, not '{str(v)[:30]}'.") from None
        if not math.isfinite(x):
            raise PlanError(f"{col} needs a finite number.")
        return x
    if kind == "datetime":
        if not isinstance(v, str) or not v.strip():
            raise PlanError(f"{col} needs a date such as 2025-03-31.")
        try:
            ts = pd.Timestamp(v.strip())
        except (ValueError, TypeError):
            raise PlanError(f"'{v[:30]}' is not a date.") from None
        if pd.isna(ts):
            raise PlanError(f"'{v[:30]}' is not a date.")
        return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts
    if kind == "boolean":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.lower() in ("true", "false"):
            return v.lower() == "true"
        raise PlanError(f"{col} needs true or false.")
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise PlanError(f"{col} needs a text value.")
    s = str(v)
    if len(s) > 200:
        raise PlanError("A filter value is too long.")
    return s


def _canon(df, col, kind, v, strict):
    """Match a text value to the real value (case-insensitively). Unknown values are refused when strict."""
    if kind not in ("categorical", "text"):
        return v
    lookup = {str(x).lower(): str(x) for x in df[col].dropna().unique()}
    hit = lookup.get(v.lower())
    if hit is not None:
        return hit
    if strict:
        raise PlanError(f"'{v[:60]}' is not a value of {col}. Some values: "
                        + ", ".join(sorted(lookup.values())[:8]))
    return v


def _value(df, col, kind, op, raw, strict):
    if op in ("is_null", "not_null"):
        return None
    if op == "between":
        if not isinstance(raw, list) or len(raw) != 2:
            raise PlanError(f"'between' on {col} needs [low, high].")
        lo, hi = _scalar(kind, raw[0], col), _scalar(kind, raw[1], col)
        if lo > hi:
            raise PlanError(f"'between' on {col}: low is greater than high.")
        return (lo, hi)
    if op == "in":
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_IN:
            raise PlanError(f"'in' on {col} needs a list of 1 to {MAX_IN} values.")
        return tuple(_canon(df, col, kind, _scalar(kind, x, col), strict) for x in raw)
    return _canon(df, col, kind, _scalar(kind, raw, col), strict)


def _filters(raw, df, kinds, strict):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FILTERS:
        raise PlanError(f"Filters must be a list of at most {MAX_FILTERS}.")
    out = []
    for f in raw:
        if not isinstance(f, dict) or set(f) - {"column", "op", "value"}:
            raise PlanError("Each filter needs column, op and value.")
        col = _col(df, f.get("column"), "Filter")
        kind, op = kinds[col], f.get("op")
        if op not in FILTER_OPS:
            raise PlanError(f"Unknown filter operation: {str(op)[:20]}.")
        if op not in OPS_BY_KIND[kind]:
            raise PlanError(f"'{op}' cannot be used on the {kind} column {col}.")
        out.append(Filter(col, op, _value(df, col, kind, op, f.get("value"), strict), kind))
    return out


def _groups(raw, df, kinds):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_GROUPS:
        raise PlanError(f"Group-by must be a list of at most {MAX_GROUPS} columns.")
    out, seen = [], set()
    for g in raw:
        if isinstance(g, str):
            g = {"column": g}
        if not isinstance(g, dict) or set(g) - {"column", "bucket"}:
            raise PlanError("Each group-by needs a column and, for dates, a bucket.")
        col = _col(df, g.get("column"), "Group-by")
        if col in seen:
            raise PlanError("A column can be used in the group-by only once.")
        seen.add(col)
        kind, bucket = kinds[col], g.get("bucket")
        if kind == "datetime":
            if bucket not in BUCKETS:
                raise PlanError(f"{col} is a date column: choose a bucket ({', '.join(BUCKETS)}).")
        else:
            if bucket is not None:
                raise PlanError(f"A bucket can only be used on date columns ({col} is not one).")
            if kind == "empty":
                raise PlanError(f"{col} has no data.")
            if kind == "numeric" and df[col].nunique() > MAX_NUMERIC_GROUP_VALUES:
                raise PlanError(f"{col} has too many distinct numbers to group by.")
        out.append(Group(col, bucket, kind))
    return out


def _aggs(raw, df, kinds, taken):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_AGGS:
        raise PlanError(f"Aggregations must be a list of at most {MAX_AGGS}.")
    out, taken = [], set(taken)
    for a in raw:
        if not isinstance(a, dict) or set(a) - {"column", "agg", "alias"}:
            raise PlanError("Each aggregation needs column and agg (alias is optional).")
        fn = a.get("agg")
        if fn not in AGGS:
            raise PlanError(f"Unknown aggregation: {str(fn)[:20]}. Allowed: {', '.join(AGGS)}.")
        if a.get("column") == "*":
            if fn != "count":
                raise PlanError("'*' can only be used with count.")
            col, default = "*", "row_count"
        else:
            col = _col(df, a.get("column"), "Aggregation")
            kind = kinds[col]
            if fn in ("sum", "mean") and kind != "numeric":
                raise PlanError(f"{fn} needs a numeric column ({col} is {kind}).")
            if fn in ("min", "max") and kind not in ("numeric", "datetime"):
                raise PlanError(f"{fn} needs a numeric or date column ({col} is {kind}).")
            default = f"{col}_{fn}"
        alias = a.get("alias")
        if alias is None:
            name = default
        elif isinstance(alias, str) and ALIAS.match(alias):
            name = alias.strip()
        else:
            raise PlanError("An alias may use letters, digits, spaces, _ - ( ) and at most 40 characters.")
        if name.lower() in taken:
            raise PlanError(f"Duplicate output name: {name}.")
        taken.add(name.lower())
        out.append(Agg(col, fn, name))
    return out


def validate_plan(raw, df, strict_values=True):
    """Return a Plan or raise PlanError. strict_values=False lets unknown filter values match nothing
    (used when comparing files, where a value may legitimately exist in only one of them)."""
    if not isinstance(raw, dict):
        raise PlanError("The plan must be a JSON object.")
    extra = set(raw) - ALLOWED_KEYS
    if extra:
        raise PlanError("Unknown plan field: " + ", ".join(sorted(str(k)[:30] for k in extra)))
    kinds = {c: column_kind(df[c]) for c in df.columns}
    chart = raw.get("chart", "auto")
    if chart not in CHARTS:
        raise PlanError(f"Unknown chart type. Allowed: {', '.join(CHARTS)}.")
    filters = _filters(raw.get("filters"), df, kinds, strict_values)
    groups = _groups(raw.get("group_by"), df, kinds)
    aggs = _aggs(raw.get("aggregations"), df, kinds, [g.column for g in groups])
    notes = []
    x, y, columns = raw.get("x"), raw.get("y"), raw.get("columns")
    top_n, sort = raw.get("top_n"), raw.get("sort")

    if chart == "scatter":
        if groups or aggs or columns is not None:
            raise PlanError("A scatter chart plots two columns without grouping or aggregation.")
        x, y = _col(df, x, "Scatter x"), _col(df, y, "Scatter y")
        if x == y or kinds[x] != "numeric" or kinds[y] != "numeric":
            raise PlanError("A scatter chart needs two different numeric columns.")
        if sort is not None or top_n is not None:
            raise PlanError("Sorting and top-N do not apply to a scatter chart.")
        return Plan("scatter", filters, chart="scatter", x=x, y=y)

    if x is not None or y is not None:
        raise PlanError("x and y are only used with a scatter chart.")

    if groups or aggs:
        if columns is not None:
            raise PlanError("'columns' cannot be combined with grouping or aggregation.")
        if groups and not aggs:
            aggs = [Agg("*", "count", "row_count")]
            notes.append("No aggregation was given, so rows are counted.")
        outs = {n.lower(): n for n in [g.column for g in groups] + [a.out for a in aggs]}
        alias = {}
        for a in aggs:
            keys = [a.column.lower()] if a.column != "*" else ["count", "rows", "row_count", "number of rows"]
            for k in keys:
                alias.setdefault(k, []).append(a.out)
        for k, v in alias.items():
            if len(v) == 1:                      # only when exactly one output matches
                outs.setdefault(k, v[0])
        sort_by, desc = None, True
        if sort is not None:
            if not isinstance(sort, dict) or set(sort) - {"by", "desc"}:
                raise PlanError("Sort needs 'by' and optionally 'desc'.")
            sort_by = outs.get(str(sort.get("by", "")).strip().lower())
            if sort_by is None:
                raise PlanError("Sort column must be one of: " + ", ".join(outs.values()))
            desc = sort.get("desc", True)
            if not isinstance(desc, bool):
                raise PlanError("'desc' must be true or false.")
        if top_n is not None and (isinstance(top_n, bool) or not isinstance(top_n, int)
                                  or not 1 <= top_n <= MAX_TOP_N):
            raise PlanError(f"top_n must be a whole number from 1 to {MAX_TOP_N}.")
        return Plan("aggregate", filters, groups, aggs, sort_by, desc, top_n, chart, notes=notes)

    if sort is not None or top_n is not None:
        raise PlanError("Sorting and top-N need a group-by or an aggregation.")
    if columns is None:
        cols = list(df.columns)[:MAX_TABLE_COLUMNS]
    else:
        if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_TABLE_COLUMNS:
            raise PlanError(f"'columns' must list 1 to {MAX_TABLE_COLUMNS} columns.")
        cols = [_col(df, c, "Table column") for c in columns]
        if len(set(cols)) != len(cols):
            raise PlanError("A table column can be listed only once.")
    return Plan("table", filters, chart=chart if chart in ("auto", "table") else "table", columns=cols)