"""Natural language -> validated SELECT. The model is untrusted; nothing here executes SQL."""
import re
from dataclasses import dataclass

from .validate import SqlRejected, check_sql

MAX_QUESTION_CHARS = 500
FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)
SELECT_START = re.compile(r"\b(with|select)\b", re.I)

SYSTEM = (
    "You write SQLite SQL. Reply with exactly ONE read-only SELECT statement and nothing else: "
    "no explanations, no comments, no markdown.\n"
    "Use only the tables and columns listed in the schema. Do not invent names. "
    "Use SQLite syntax only (no NULLS LAST, no ILIKE, no schema prefixes). "
    "If the question cannot be answered with the schema, reply with exactly: CANNOT_ANSWER"
)


@dataclass
class Generation:
    status: str              # ok | rejected | cannot_answer
    sql: str = ""
    raw: str = ""
    reason: str = ""
    checked: object = None   # validate.Checked when status == "ok"


def schema_text(schema):
    lines = []
    for t in schema["tables"]:
        head = f"{t['kind'].upper()} {t['name']}" + (f" -- {t['description']}" if t.get("description") else "")
        lines.append(head)
        pk = set(t["primary_key"])
        for c in t["columns"]:
            note = " PRIMARY KEY" if c["name"] in pk else ""
            desc = f" -- {c['description']}" if c.get("description") else ""
            lines.append(f"  {c['name']} {c['type'] or 'ANY'}{note}{desc}")
        for fk in t["foreign_keys"]:
            lines.append(f"  FOREIGN KEY ({', '.join(fk['from'])}) REFERENCES {fk['table']}({', '.join(fk['to'])})")
    for r in schema.get("declared_relationships", []):
        lines.append(f"RELATIONSHIP {r['from']} = {r['to']}")
    return "\n".join(lines)


def build_chat_messages(question, schema):
    user = f"SCHEMA:\n{schema_text(schema)}\n\nQUESTION: {question}\n\nSQL:"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def build_completion_prompt(question, schema):
    """SQLCoder-style prompt."""
    return (f"### Task\nGenerate a SQLite query to answer [QUESTION]{question}[/QUESTION]\n\n"
            f"### Database Schema\n{schema_text(schema)}\n\n"
            f"### Answer\nGiven the database schema, here is the SQLite query that answers "
            f"[QUESTION]{question}[/QUESTION]\n[SQL]\n")


def clean_output(raw):
    """Strip fences, markers, trailing semicolons and PostgreSQL-only NULLS ordering."""
    text = (raw or "").strip()
    m = FENCE.search(text)
    if m:
        text = m.group(1).strip()
    text = text.replace("[/SQL]", "").replace("[SQL]", "").strip()
    s = SELECT_START.search(text)
    if s:
        text = text[s.start():]
    text = re.sub(r"\s+NULLS\s+(FIRST|LAST)\b", "", text, flags=re.I)
    return text.strip().rstrip(";").strip()


def generate_sql(question, schema, llm, model_id, model_cfg):
    question = (question or "").strip()
    if not question:
        return Generation("rejected", reason="Please enter a question.")
    if len(question) > MAX_QUESTION_CHARS:
        return Generation("rejected", reason=f"The question is too long (limit {MAX_QUESTION_CHARS} characters).")
    if not schema.get("tables"):
        return Generation("rejected", reason="The selected database has no tables.")
    llm.start(model_id)
    if model_cfg.get("prompt_format") == "completion":
        raw = llm.complete(build_completion_prompt(question, schema), max_tokens=300, stop=["[/SQL]", "###"])
    else:
        raw = llm.chat(build_chat_messages(question, schema), max_tokens=300)
    if "CANNOT_ANSWER" in raw.upper():
        return Generation("cannot_answer", raw=raw, reason="The database schema cannot answer this question.")
    sql = clean_output(raw)
    try:
        checked = check_sql(sql, schema)
    except SqlRejected as exc:
        return Generation("rejected", sql=sql, raw=raw, reason=str(exc))
    return Generation("ok", sql=checked.sql, raw=raw, checked=checked)