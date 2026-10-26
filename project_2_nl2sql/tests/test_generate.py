import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_sample_db  # noqa: E402
from docsql import generate as g  # noqa: E402
from docsql.catalog import DatabaseCatalog  # noqa: E402


class Fake:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def start(self, mid):
        pass

    def chat(self, messages, **kw):
        self.sent.append(messages)
        return self.reply

    def complete(self, prompt, **kw):
        self.sent.append(prompt)
        return self.reply


@pytest.fixture
def schema(tmp_path):
    d = tmp_path / "dbs"
    d.mkdir()
    make_sample_db.build(d / "shop.sqlite")
    return DatabaseCatalog(d, tmp_path / "cache").get_schema("db:shop.sqlite")


CHAT, COMP = {}, {"prompt_format": "completion"}


def test_prompt_contains_live_schema_and_relationships(schema):
    msgs = g.build_chat_messages("How many customers?", schema)
    text = msgs[1]["content"]
    assert "TABLE customers" in text and "VIEW customer_orders" in text
    assert "FOREIGN KEY (customer_id) REFERENCES customers(id)" in text
    assert "price REAL" in text and "How many customers?" in msgs[1]["content"]
    assert "ONE read-only SELECT" in msgs[0]["content"]


@pytest.mark.parametrize("raw,expected", [
    ("SELECT COUNT(*) FROM customers;", "SELECT COUNT(*) FROM customers"),
    ("```sql\nSELECT COUNT(*) FROM customers;\n```", "SELECT COUNT(*) FROM customers"),
    ("Here is the query:\nSELECT name FROM customers", "SELECT name FROM customers"),
    ("SELECT name FROM customers ORDER BY name NULLS LAST;\n[SQL]", "SELECT name FROM customers ORDER BY name"),
    ("[SQL]\nSELECT 1 [/SQL]", "SELECT 1"),
])
def test_output_cleanup(raw, expected):
    assert g.clean_output(raw) == expected


def test_valid_generation_is_checked_and_not_executed(schema):
    r = g.generate_sql("How many customers?", schema, Fake("SELECT COUNT(*) FROM customers;"), "m", CHAT)
    assert r.status == "ok" and r.checked is not None and r.sql.upper().startswith("SELECT")


def test_completion_format_used_for_sqlcoder(schema):
    f = Fake("SELECT COUNT(*) FROM customers ORDER BY 1 NULLS LAST;\n[SQL]")
    r = g.generate_sql("How many customers?", schema, f, "m", COMP)
    assert r.status == "ok" and isinstance(f.sent[0], str) and "[QUESTION]" in f.sent[0]


@pytest.mark.parametrize("raw", [
    "DROP TABLE customers", "SELECT * FROM secrets", "SELECT password FROM customers",
    "SELECT 1; DELETE FROM customers", "SELECT load_extension('x')", "nothing useful here",
])
def test_unsafe_or_wrong_model_output_is_rejected_with_reason(schema, raw):
    r = g.generate_sql("anything", schema, Fake(raw), "m", CHAT)
    assert r.status == "rejected" and r.reason and r.checked is None


def test_cannot_answer_and_input_limits(schema):
    assert g.generate_sql("q", schema, Fake("CANNOT_ANSWER"), "m", CHAT).status == "cannot_answer"
    assert g.generate_sql("", schema, Fake("x"), "m", CHAT).status == "rejected"
    assert g.generate_sql("q" * 501, schema, Fake("x"), "m", CHAT).status == "rejected"
    assert g.generate_sql("q", {"tables": []}, Fake("x"), "m", CHAT).status == "rejected"