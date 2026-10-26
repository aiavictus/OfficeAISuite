"""Deterministic analysis engine: validated plan -> pandas -> table, metrics and a chart recommendation.
No model is involved and no generated code is executed."""
import datetime as dt

import numpy as np
import pandas as pd

from . import config
from .plan import PlanError, validate_plan

MAX_SERIES = 8
MAX_BAR_CATEGORIES = 40
MAX_SCATTER_POINTS = 5000
MISSING = "(missing)"
FN_WORD = {"sum": "Total", "mean": "Average", "count": "Count", "min": "Minimum", "max": "Maximum",
           "nunique": "Distinct count"}
_FN = {"sum": lambda s: s.sum(min_count=1), "mean": lambda s: s.mean(), "count": lambda s: s.count(),
       "min": lambda s: s.min(), "max": lambda s: s.max(), "nunique": lambda s: s.nunique()}


def _py(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if v != v else round(float(v), 6)
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        return v.isoformat()
    return v


def _rows(df):
    return [[_py(v) for v in r] for r in df.itertuples(index=False, name=None)]


# ---------------------------------------------------------------- filtering
def _one(s, f):
    op, v = f.op, f.value
    if op == "is_null":
        return s.isna()
    if op == "not_null":
        return s.notna()
    if f.kind in ("categorical", "text"):
        t, ok = s.astype(str), s.notna()
        if op == "eq":
            return ok & (t == v)
        if op == "ne":
            return ok & (t != v)
        return ok & t.isin(list(v))
    if op == "eq":
        return s == v
    if op == "ne":
        return s.notna() & (s != v)
    if op == "in":
        return s.isin(list(v))
    if op == "gt":
        return s > v
    if op == "gte":
        return s >= v
    if op == "lt":
        return s < v
    if op == "lte":
        return s <= v
    lo, hi = v
    return (s >= lo) & (s <= hi)


def _mask(df, filters):
    mask = pd.Series(True, index=df.index)
    for f in filters:
        mask &= _one(df[f.column], f)
    return mask


# ---------------------------------------------------------------- aggregation
def _key(sub, g):
    s = sub[g.column]
    if g.bucket:
        if g.bucket == "day":
            lab = s.dt.strftime("%Y-%m-%d")
        elif g.bucket == "month":
            lab = s.dt.strftime("%Y-%m")
        elif g.bucket == "year":
            lab = s.dt.strftime("%Y")
        else:
            lab = s.dt.year.astype("Int64").astype(str) + "-Q" + s.dt.quarter.astype("Int64").astype(str)
        lab = lab.where(s.notna(), MISSING)
    elif g.kind == "numeric":
        lab = s
    else:
        lab = s.astype(object).where(s.notna(), MISSING).astype(str)
    return lab.rename(g.column)


def _scalar(sub, a):
    return len(sub) if a.column == "*" else _FN[a.fn](sub[a.column])


def _aggregate(sub, plan, notes):
    out_cols = [g.column for g in plan.groups] + [a.out for a in plan.aggs]
    if not plan.groups:
        return pd.DataFrame([{a.out: _scalar(sub, a) for a in plan.aggs}])
    if sub.empty:
        return pd.DataFrame(columns=out_cols)
    keys, keep = [], pd.Series(True, index=sub.index)
    for g in plan.groups:
        k = _key(sub, g)
        if g.kind == "numeric" and not g.bucket:
            lost = int(k.isna().sum())
            if lost:
                notes.append(f"{lost} row(s) with a missing {g.column} were left out of the grouping.")
                keep &= k.notna()
        keys.append(k)
    if not keep.all():
        sub, keys = sub[keep], [k[keep] for k in keys]
    if sub.empty:
        return pd.DataFrame(columns=out_cols)
    grouped = sub.groupby(keys, sort=False)
    parts = []
    for a in plan.aggs:
        p = grouped.size() if a.column == "*" else _FN[a.fn](grouped[a.column])
        parts.append(p.rename(a.out))
    return pd.concat(parts, axis=1).reset_index()


# ---------------------------------------------------------------- charts
def _label(a):
    return "Number of rows" if a.column == "*" else f"{FN_WORD[a.fn]} of {a.column}"


def _date_group(plan):
    return next((g.column for g in plan.groups if g.bucket), None)


def _fits(kind, plan, res):
    out = [a.out for a in plan.aggs]
    if kind == "metric":
        return not plan.groups
    if not plan.groups:
        return kind == "table"
    if kind == "table":
        return True
    if kind == "pie":
        col = res[out[0]]
        return (len(plan.groups) == 1 and len(out) == 1 and plan.aggs[0].fn in ("sum", "count", "nunique")
                and 0 < len(res) <= 12 and bool(col.notna().all()) and bool((col >= 0).all()))
    date_g = _date_group(plan)
    if kind == "line" and not date_g:
        return False
    x = date_g if kind == "line" else plan.groups[0].column
    others = [g.column for g in plan.groups if g.column != x]
    if others and (len(out) > 1 or res[others[0]].nunique() > MAX_SERIES):
        return False
    return not (kind == "bar" and res[x].nunique() > MAX_BAR_CATEGORIES)


def _spec(kind, plan, out):
    gcols = [g.column for g in plan.groups]
    if kind in ("table", "metric") or not gcols:
        return {"type": kind, "x": None, "y": out, "series": None}
    x = _date_group(plan) if kind == "line" else gcols[0]
    others = [c for c in gcols if c != x]
    series = others[0] if kind in ("bar", "line") and others and len(out) == 1 else None
    return {"type": kind, "x": x, "y": out[:1] if kind == "pie" else out, "series": series}


def choose_chart(plan, res):
    out = [a.out for a in plan.aggs]
    if not plan.groups:
        note = None
        if plan.chart not in ("auto", "metric", "table"):
            note = f"A single result cannot be drawn as a {plan.chart} chart, so it is shown as a metric."
        return _spec("metric", plan, out), note
    if res.empty:
        return _spec("table", plan, out), None
    pick = next(k for k in ("line", "bar", "table") if _fits(k, plan, res))
    if plan.chart == "auto":
        return _spec(pick, plan, out), None
    if _fits(plan.chart, plan, res):
        return _spec(plan.chart, plan, out), None
    return _spec(pick, plan, out), f"A {plan.chart} chart does not suit this result, so a {pick} chart is shown instead."


# ---------------------------------------------------------------- description
def _fmt(x):
    if isinstance(x, pd.Timestamp):
        return x.date().isoformat() if x == x.normalize() else x.isoformat(sep=" ")
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _fdesc(f):
    if f.op == "is_null":
        return f"{f.column} is empty"
    if f.op == "not_null":
        return f"{f.column} is not empty"
    if f.op == "between":
        return f"{f.column} is between {_fmt(f.value[0])} and {_fmt(f.value[1])}"
    if f.op == "in":
        return f"{f.column} is one of " + ", ".join(_fmt(x) for x in f.value)
    word = {"eq": "is", "ne": "is not", "gt": "is above", "gte": "is at least", "lt": "is below",
            "lte": "is at most"}[f.op]
    return f"{f.column} {word} {_fmt(f.value)}"


def _describe(plan, matched, total, with_rows=True):
    if plan.mode == "scatter":
        text = f"{plan.y} plotted against {plan.x}"
    elif plan.mode == "table":
        text = "The rows of the data (first columns shown)"
    else:
        text = ", ".join(_label(a) for a in plan.aggs)
        if plan.groups:
            text += " by " + ", ".join(g.column + (f" ({g.bucket})" if g.bucket else "") for g in plan.groups)
    if plan.filters:
        text += ", where " + "; ".join(_fdesc(f) for f in plan.filters)
    text += "."
    if plan.mode == "aggregate" and plan.groups:
        if plan.sort_by:
            text += f" Sorted by {plan.sort_by} ({'highest' if plan.desc else 'lowest'} first)."
        if plan.top_n:
            text += f" Top {plan.top_n} shown."
    if with_rows:
        text += f" Based on {matched} of {total} rows."
    return text


# ---------------------------------------------------------------- running a plan
def run_plan(df, plan):
    total = len(df)
    sub = df[_mask(df, plan.filters)] if plan.filters else df
    matched = len(sub)
    notes = list(plan.notes)
    if matched == 0:
        notes.append("No rows match the filters.")
    out = {"mode": plan.mode, "rows_total": total, "rows_matched": matched, "truncated": False,
           "description": _describe(plan, matched, total)}
    rows_metric = {"label": "Rows analysed", "value": matched, "of": total}

    if plan.mode == "table":
        part = sub[plan.columns].head(config.TABLE_PREVIEW_ROWS)
        if matched > len(part):
            out["truncated"] = True
            notes.append(f"Only the first {len(part)} rows are shown.")
        out.update(columns=list(plan.columns), rows=_rows(part), chart={"type": "table", "x": None, "y": [], "series": None},
                   metrics=[rows_metric])
    elif plan.mode == "scatter":
        pts = sub[[plan.x, plan.y]].dropna()
        if matched - len(pts):
            notes.append(f"{matched - len(pts)} row(s) with a missing value were left out.")
        if len(pts) > MAX_SCATTER_POINTS:
            out["truncated"] = True
            notes.append(f"Only the first {MAX_SCATTER_POINTS} points are drawn.")
            pts = pts.head(MAX_SCATTER_POINTS)
        out.update(columns=[plan.x, plan.y], rows=_rows(pts),
                   chart={"type": "scatter", "x": plan.x, "y": [plan.y], "series": None}, metrics=[rows_metric])
    else:
        res = _aggregate(sub, plan, notes)
        if plan.groups and not res.empty:
            keys = [g.column for g in plan.groups]
            if plan.sort_by:
                by, desc = plan.sort_by, plan.desc
            elif _date_group(plan):
                by, desc = _date_group(plan), False
            else:
                by, desc = plan.aggs[0].out, True
            by_list = [by] + [k for k in keys if k != by]
            res = res.sort_values(by_list, ascending=[not desc] + [True] * (len(by_list) - 1), kind="mergesort")
            if plan.top_n:
                res = res.head(plan.top_n)
            if len(res) > config.MAX_RESULT_ROWS:
                res = res.head(config.MAX_RESULT_ROWS)
                out["truncated"] = True
                notes.append(f"Only the first {config.MAX_RESULT_ROWS} rows are shown.")
        res = res.reset_index(drop=True)
        chart, cnote = choose_chart(plan, res)
        if cnote:
            notes.append(cnote)
        if plan.groups:
            metrics = [rows_metric]
            for a in plan.aggs:
                if a.fn in ("sum", "count"):
                    metrics.append({"label": "Overall " + _label(a).lower(), "value": _py(_scalar(sub, a))})
        else:
            row = res.iloc[0]
            metrics = [{"label": _label(a), "value": _py(row[a.out])} for a in plan.aggs] + [rows_metric]
        out.update(columns=list(res.columns), rows=_rows(res), chart=chart, metrics=metrics)
    out["notes"] = notes
    return out


def execute(df, raw_plan, strict_values=True):
    """Validate an untrusted plan against the real table and run it."""
    return run_plan(df, validate_plan(raw_plan, df, strict_values))


# ---------------------------------------------------------------- comparing files
def compare(tables, raw_plan):
    """Run ONE plan on 2..MAX_COMPARE tables ({"name", "df"}) and combine the results with a source column."""
    if not isinstance(tables, list) or not 2 <= len(tables) <= config.MAX_COMPARE:
        raise PlanError(f"Choose between 2 and {config.MAX_COMPARE} files to compare.")
    names = [t["name"] for t in tables]
    if len(set(names)) != len(names):
        raise PlanError("The compared files must have different names.")
    if not isinstance(raw_plan, dict):
        raise PlanError("The plan must be a JSON object.")
    if raw_plan.get("top_n") is not None:
        raise PlanError("Top-N is not supported when comparing files.")
    plans, results = [], []
    for t in tables:
        try:
            plan = validate_plan(raw_plan, t["df"], strict_values=False)
        except PlanError as exc:
            raise PlanError(f"{t['name']}: {exc}") from exc
        if plan.mode != "aggregate" or len(plan.groups) > 1 or len(plan.aggs) != 1:
            raise PlanError("A comparison needs exactly one aggregation and at most one group-by.")
        plans.append(plan)
        results.append(run_plan(t["df"], plan))
    g0 = plans[0].groups[0] if plans[0].groups else None
    if len({bool(p.groups) and p.groups[0].kind == "numeric" for p in plans}) > 1:
        raise PlanError("The group-by column holds different kinds of values in the compared files.")
    cols = ["source"] + results[0]["columns"]
    rows, notes, metrics = [], [], []
    for t, r in zip(tables, results):
        rows += [[t["name"]] + row for row in r["rows"]]
        metrics.append({"label": f"{t['name']}: rows analysed", "value": r["rows_matched"], "of": r["rows_total"]})
        notes += [f"{t['name']}: {n}" for n in r["notes"]]
    if g0 is not None:
        numeric = g0.kind == "numeric"
        rows.sort(key=lambda r: (r[1] is None, r[1] if r[1] is not None else (0 if numeric else "")))
    kind = "bar" if g0 is None or not g0.bucket else "line"
    want = plans[0].chart
    if want == "table":
        kind = "table"
    elif want not in ("auto", kind):
        notes.append(f"A {want} chart does not suit this comparison, so a {kind} chart is shown instead.")
    if not rows:
        kind = "table"
    elif kind == "bar" and g0 is not None and len({r[1] for r in rows}) > MAX_BAR_CATEGORIES:
        kind = "table"
        notes.append("Too many categories for a bar chart: shown as a table. Filter or group differently.")
    out_name = results[0]["columns"][-1]
    chart = {"type": kind, "x": None if kind == "table" else ("source" if g0 is None else cols[1]),
             "y": [out_name], "series": "source" if g0 is not None and kind != "table" else None}
    desc = _describe(plans[0], 0, 0, with_rows=False) + " Compared across: " + ", ".join(names) + "."
    return {"mode": "compare", "columns": cols, "rows": rows, "chart": chart, "metrics": metrics,
            "notes": notes, "description": desc, "sources": names, "truncated": False}