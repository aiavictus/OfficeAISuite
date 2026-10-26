"""Run questions through a real model; compare RESULT SETS with known-correct SQL.
    python project_2_nl2sql\\tests\\benchmark_sql.py qwen2.5-coder-7b qwen3-8b qwen3-4b sqlcoder-7b
Model output varies: repeat runs. Row order is ignored unless the question asks for ordering."""
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_sample_db  # noqa: E402
from docsql import executor, generate  # noqa: E402
from docsql.catalog import DatabaseCatalog  # noqa: E402
from shared.llm_manager import LLMError, LlamaServer, load_models  # noqa: E402

QUESTIONS = [
    ("How many customers are there?", "SELECT COUNT(*) FROM customers"),
    ("List the names of customers living in Pune.", "SELECT name FROM customers WHERE city = 'Pune'"),
    ("How many customers live in each city?", "SELECT city, COUNT(*) FROM customers GROUP BY city"),
    ("What is the most expensive product's name?", "SELECT name FROM products ORDER BY price DESC LIMIT 1"),
    ("How many orders has each customer placed? Show customer name and count, including customers with none.",
     "SELECT c.name, COUNT(o.id) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id"),
    ("Which customers have never placed an order?",
     "SELECT name FROM customers WHERE id NOT IN (SELECT customer_id FROM orders)"),
    ("What is the total quantity ordered for each product name?",
     "SELECT p.name, SUM(oi.qty) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id"),
    ("What is the total sales value (quantity times price) per customer name?",
     "SELECT c.name, SUM(oi.qty * p.price) FROM customers c JOIN orders o ON o.customer_id = c.id "
     "JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id GROUP BY c.id"),
    ("How many orders were placed in February 2025?",
     "SELECT COUNT(*) FROM orders WHERE ordered_on >= '2025-02-01' AND ordered_on < '2025-03-01'"),
    ("What is the average product price?", "SELECT AVG(price) FROM products"),
    ("Which city has the most customers?",
     "SELECT city FROM customers GROUP BY city ORDER BY COUNT(*) DESC LIMIT 1"),
    ("What is the email address of Asha?", None),      # not in schema: expect CANNOT_ANSWER or a rejection
]


def norm(rows):
    return sorted((tuple(round(v, 4) if isinstance(v, float) else v for v in r) for r in rows), key=repr)


def main(models):
    cfgs = {m["id"]: m for m in load_models()}
    tmp = Path(tempfile.mkdtemp())
    (tmp / "dbs").mkdir()
    path = make_sample_db.build(tmp / "dbs" / "shop.sqlite")
    cat = DatabaseCatalog(tmp / "dbs", tmp / "cache")
    schema, rec = cat.get_schema("db:shop.sqlite"), cat.resolve("db:shop.sqlite")
    with closing(sqlite3.connect(path)) as con:
        truth = [None if sql is None else norm(con.execute(sql).fetchall()) for _, sql in QUESTIONS]
    srv = LlamaServer(profile="cpu_16gb", port=8088)
    try:
        for mid in models:
            cfg, passed, t0 = cfgs[mid], 0, time.time()
            print(f"\n=== {mid} ===")
            for (q, _), exp in zip(QUESTIONS, truth):
                try:
                    gen = generate.generate_sql(q, schema, srv, mid, cfg)
                    if exp is None:
                        ok, why = gen.status in ("cannot_answer", "rejected"), gen.status
                    elif gen.status != "ok":
                        ok, why = False, f"{gen.status}: {gen.reason[:70]}"
                    else:
                        got = norm(executor.run_query(rec, gen.checked).rows)
                        ok, why = got == exp, "ok" if got == exp else f"wrong rows: {gen.sql[:90]}"
                except (LLMError, executor.QueryError) as exc:
                    ok, why = False, f"error: {str(exc)[:80]}"
                passed += ok
                print(f"[{'PASS' if ok else 'FAIL'}] {q[:55]:<55} {why}")
            print(f"--> {mid}: {passed}/{len(QUESTIONS)} in {time.time() - t0:.0f}s")
            srv.stop()
    finally:
        srv.stop()


if __name__ == "__main__":
    main(sys.argv[1:] or ["qwen2.5-coder-7b"])