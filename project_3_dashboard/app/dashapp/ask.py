"""Free-text request -> validated plan -> engine. The model sees column metadata only and never computes."""
import json
import re

from .plan import PlanError, validate_plan
from .profile import column_kind

MAX_REQUEST_CHARS = 400
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)

SYSTEM = (
    "You turn a request about a spreadsheet into ONE JSON analysis plan. Reply with JSON only: no text, no markdown.\n"
    "Plan fields (all optional):\n"
    '  "filters": [{"column": C, "op": "eq|ne|in|gt|gte|lt|lte|between|is_null|not_null", "value": V}]\n'
    '  "group_by": [C] or [{"column": DATE_COLUMN, "bucket": "day|month|quarter|year"}]   (at most 2)\n'
    '  "aggregations": [{"column": C or "*", "agg": "sum|mean|count|min|max|nunique"}]   ("*" only with count)\n'
    '  "sort": {"by": OUTPUT_NAME, "desc": true}, "top_n": 1..100\n'
    "Output names: group columns keep their name; an aggregation is named COLUMN_AGG "
    '(for example Units_sum, Revenue_mean) and a count of "*" is named row_count. '
    '"sort.by" must be one of those names. top_n without sort already ranks by the first aggregation, highest first.\n'
    '  "chart": "auto|bar|line|pie|scatter|table|metric"   (scatter needs "x" and "y" numeric columns and nothing else)\n'
    "Rules: use ONLY the listed column names and, for filters on text columns, ONLY the listed values. "
    "sum and mean need numeric columns. Date columns need a bucket in group_by. "
    'For "show the data" use {"chart": "table"}. '
    'If the request cannot be answered with these columns, reply exactly: {"cannot_answer": true}'
)


class AskError(ValueError):
    """Message is safe to show to the user."""


def describe(df, max_values=12):
    lines = []
    for c in df.columns:
        kind = column_kind(df[c])
        extra = ""
        if kind == "categorical":
            vals = [str(v) for v in df[c].dropna().unique()][:max_values]
            extra = " values: " + ", ".join(vals)
        elif kind == "numeric":
            extra = f" range: {df[c].min()} to {df[c].max()}"
        elif kind == "datetime":
            extra = f" range: {df[c].min().date()} to {df[c].max().date()}"
        lines.append(f"- {c} ({kind}){extra}")
    return "\n".join(lines)


def build_messages(request, df):
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"COLUMNS:\n{describe(df)}\n\nREQUEST: {request}\n\nJSON plan:"}]


def extract_json(raw):
    text = (raw or "").strip()
    m = FENCE.search(text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise AskError("The model did not return a plan.")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        raise AskError("The model's plan was not valid JSON.") from None
    if not isinstance(obj, dict):
        raise AskError("The model's plan was not a JSON object.")
    return obj


def plan_from_request(request, df, llm, model_id):
    request = (request or "").strip()
    if not request:
        raise AskError("Please enter a request.")
    if len(request) > MAX_REQUEST_CHARS:
        raise AskError(f"The request is too long (limit {MAX_REQUEST_CHARS} characters).")
    llm.start(model_id)
    raw = llm.chat(build_messages(request, df), max_tokens=400)
    plan = extract_json(raw)
    if plan.get("cannot_answer") is True:
        return None
    try:
        validate_plan(plan, df)
    except PlanError as exc:
        raise AskError(f"The model proposed a plan that does not fit your data: {exc}") from exc
    return plan