"""SQL validation: one read-only SELECT, allowlisted constructs, tables and columns from the live schema.

The model's SQL is never trusted. It is parsed with sqlglot, checked node by node against an
allowlist, checked against the schema, regenerated from the AST and validated a second time.
The REGENERATED text is what gets executed.
"""
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

MAX_SQL_CHARS = 4000
ROOTS = frozenset({"Select", "Union", "Intersect", "Except"})

ALLOWED_NODES = frozenset({
    "Select", "Union", "Intersect", "Except", "Subquery", "With", "CTE", "Table", "TableAlias",
    "Column", "Identifier", "Star", "Alias", "Literal", "Null", "Boolean", "Paren", "Tuple",
    "Where", "Group", "Having", "Order", "Ordered", "Limit", "Offset", "Join", "From", "Distinct",
    "And", "Or", "Not", "EQ", "NEQ", "GT", "GTE", "LT", "LTE", "Add", "Sub", "Mul", "Div", "Mod", "Neg",
    "Like", "ILike", "Escape", "In", "Between", "Is", "Exists", "Case", "If", "Cast", "DataType",
    "DataTypeParam", "DPipe", "Anonymous",
    "Count", "Sum", "Avg", "Min", "Max", "Coalesce", "Nullif", "Lower", "Upper", "Length", "Abs",
    "Round", "Substring", "Trim", "GroupConcat", "TimeToStr", "TsOrDsToDate", "TsOrDsToTimestamp",
})

# SQLite function names that queries may call (also enforced by the authorizer at run time).
SAFE_FUNCTIONS = frozenset({
    "count", "sum", "avg", "min", "max", "total", "coalesce", "ifnull", "nullif", "iif",
    "lower", "upper", "length", "abs", "round", "substr", "substring", "trim", "ltrim", "rtrim",
    "replace", "instr", "date", "datetime", "time", "strftime", "julianday", "group_concat",
    "like", "glob",
})


class SqlRejected(ValueError):
    """The SQL was refused. The message is safe to show to the user."""


@dataclass
class Checked:
    sql: str                                  # canonical text that will be executed
    tables: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    schema_sha: str = ""
    all_tables: list = field(default_factory=list)   # every table/view in the live schema (for the authorizer)


def _parse_one(sql):
    if not isinstance(sql, str):
        raise SqlRejected("The SQL must be text.")
    s = sql.strip()
    if not s:
        raise SqlRejected("The SQL is empty.")
    if len(s) > MAX_SQL_CHARS:
        raise SqlRejected(f"The SQL is too long (limit {MAX_SQL_CHARS} characters).")
    if "\x00" in s:
        raise SqlRejected("The SQL contains an invalid character.")
    if "--" in s or "/*" in s or "*/" in s:
        raise SqlRejected("Comments are not allowed.")
    try:
        stmts = [e for e in sqlglot.parse(s, read="sqlite") if e is not None]
    except Exception as exc:  # noqa: BLE001  any parser failure means: reject
        raise SqlRejected("The SQL could not be parsed.") from exc
    if len(stmts) != 1:
        raise SqlRejected("Exactly one statement is allowed.")
    return stmts[0]


def _check_nodes(root):
    if type(root).__name__ not in ROOTS:
        raise SqlRejected(f"Only SELECT queries are allowed (found {type(root).__name__}).")
    for node in root.find_all(exp.Expression):
        name = type(node).__name__
        if name not in ALLOWED_NODES:
            raise SqlRejected(f"'{name}' is not allowed in a query.")
        if name == "Anonymous" and str(node.name).lower() not in SAFE_FUNCTIONS:
            raise SqlRejected(f"The function '{str(node.name)[:40]}' is not allowed.")
        if name == "With" and node.args.get("recursive"):
            raise SqlRejected("Recursive queries are not allowed.")
        if name == "Table":
            if node.args.get("db") or node.args.get("catalog"):
                raise SqlRejected("Qualified table names (schema.table) are not allowed.")
            if not isinstance(node.this, exp.Identifier):
                raise SqlRejected("Table functions are not allowed.")


def _derived_columns(node):
    try:
        names = [n for n in node.this.named_selects]
    except Exception:  # noqa: BLE001
        names = ["*"]
    try:
        aliased = list(node.alias_column_names)
    except Exception:  # noqa: BLE001
        aliased = []
    return {str(n).lower() for n in (aliased or names)}


def _check_schema(root, schema):
    real = {t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in schema["tables"]}
    scopes = {}                                  # alias or name -> ("real", table) | ("derived", columns)
    cte_cols = {}
    for cte in root.find_all(exp.CTE):
        cte_cols[cte.alias_or_name.lower()] = _derived_columns(cte)
    used_tables = set()
    for t in root.find_all(exp.Table):
        tn = t.name.lower()
        key = (t.alias_or_name or tn).lower()
        if tn in cte_cols:
            scopes[key] = ("derived", cte_cols[tn])
        elif tn in real:
            scopes[key] = ("real", tn)
            used_tables.add(tn)
        else:
            raise SqlRejected(f"Unknown table: {t.name[:60]}")
    for sq in root.find_all(exp.Subquery):
        if sq.alias:
            scopes[sq.alias.lower()] = ("derived", _derived_columns(sq))
    loose = set(a.alias.lower() for a in root.find_all(exp.Alias) if a.alias)
    for tn in used_tables:
        loose |= real[tn]
    derived_any = False
    for kind, val in scopes.values():
        if kind == "derived":
            loose |= val
            derived_any = derived_any or "*" in val
    used_cols = set()
    for col in root.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            if col.table and col.table.lower() not in scopes:
                raise SqlRejected(f"Unknown table alias: {col.table[:60]}")
            continue
        cname, q = col.name.lower(), (col.table or "").lower()
        if q:
            if q not in scopes:
                raise SqlRejected(f"Unknown table or alias: {col.table[:60]}")
            kind, val = scopes[q]
            ok = cname in real[val] if kind == "real" else (cname in val or "*" in val)
        else:
            ok = cname in loose or derived_any
        if not ok:
            raise SqlRejected(f"Unknown column: {col.name[:60]}")
        used_cols.add(cname)
    return sorted(used_tables), sorted(used_cols)


def _analyze(sql, schema):
    root = _parse_one(sql)
    _check_nodes(root)
    tables, cols = _check_schema(root, schema)
    return root, tables, cols


def check_sql(sql, schema):
    """Validate model-written SQL against the live schema. Returns Checked or raises SqlRejected."""
    try:
        root1, _, _ = _analyze(sql, schema)
        generated = root1.sql(dialect="sqlite")
        root2, tables, cols = _analyze(generated, schema)      # second pass on what will really run
        final = root2.sql(dialect="sqlite")
    except SqlRejected:
        raise
    except RecursionError as exc:
        raise SqlRejected("The SQL is too deeply nested.") from exc
    except Exception as exc:  # noqa: BLE001
        raise SqlRejected("The SQL could not be validated.") from exc
    return Checked(sql=final, tables=tables, columns=cols, schema_sha=schema.get("sha256", ""),
                   all_tables=[t["name"] for t in schema["tables"]])