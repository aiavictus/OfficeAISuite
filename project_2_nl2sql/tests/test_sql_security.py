import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_sample_db  # noqa: E402
from docsql import catalog as cat, executor as ex  # noqa: E402
from docsql.catalog import DatabaseCatalog  # noqa: E402
from docsql.validate import SqlRejected, check_sql  # noqa: E402

SHOP = "db:shop.sqlite"


@pytest.fixture
def env(tmp_path):
    d = tmp_path / "dbs"
    d.mkdir()
    path = make_sample_db.build(d / "shop.sqlite")
    os.utime(path, ns=(1000 * 10**9, 1000 * 10**9))
    c = DatabaseCatalog(d, tmp_path / "cache")
    return c, path, c.get_schema(SHOP), c.resolve(SHOP)


def plain(path, sql):
    with closing(sqlite3.connect(path)) as con:
        return sorted((tuple(r) for r in con.execute(sql).fetchall()), key=repr)


LEGIT = [
    "SELECT COUNT(*) FROM customers",
    "SELECT city, COUNT(*) AS n FROM customers GROUP BY city ORDER BY n DESC, city",
    "SELECT c.name, SUM(oi.qty * p.price) AS total FROM customers c JOIN orders o ON o.customer_id = c.id "
    "JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id GROUP BY c.name ORDER BY c.name",
    "WITH t AS (SELECT city, COUNT(*) AS n FROM customers GROUP BY city) SELECT city, n FROM t WHERE n > 1",
    "SELECT name FROM customers WHERE id IN (SELECT customer_id FROM orders) ORDER BY name",
    "SELECT * FROM customer_orders ORDER BY name",
    "SELECT name, price FROM products WHERE price BETWEEN 15 AND 25 ORDER BY price LIMIT 2",
    "SELECT name FROM customers WHERE city = 'Pune' OR name LIKE 'D%' ORDER BY name",
    "SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL",
    "SELECT city FROM customers UNION SELECT name FROM products ORDER BY 1",
    "SELECT name, CASE WHEN price > 20 THEN 'high' ELSE 'low' END AS band FROM products ORDER BY name",
    "SELECT p.name, a.total FROM products p JOIN (SELECT product_id, SUM(qty) AS total FROM order_items "
    "GROUP BY product_id) a ON a.product_id = p.id ORDER BY p.name",
    "SELECT MAX(price), MIN(price), AVG(price) FROM products",
    "SELECT UPPER(name), LENGTH(name) FROM customers ORDER BY name;",
    "SELECT ROUND(AVG(price), 2) FROM products",
]


@pytest.mark.parametrize("sql", LEGIT)
def test_legitimate_queries_validate_and_match_plain_sqlite(env, sql):
    c, path, schema, rec = env
    checked = check_sql(sql, schema)
    res = ex.run_query(rec, checked)
    assert sorted((tuple(r) for r in res.rows), key=repr) == plain(path, sql)
    assert not res.truncated


def test_date_function_is_supported(env):
    c, path, schema, rec = env
    sql = "SELECT strftime('%Y-%m', ordered_on) AS m, COUNT(*) FROM orders GROUP BY m ORDER BY m"
    res = ex.run_query(rec, check_sql(sql, schema))
    assert sorted((tuple(r) for r in res.rows), key=repr) == plain(path, sql)


MALICIOUS = [
    "SELECT 1; DROP TABLE customers", "SELECT * FROM customers; SELECT * FROM products",
    "DROP TABLE customers", "INSERT INTO customers(name) VALUES ('x')", "UPDATE customers SET name = 'x'",
    "DELETE FROM customers", "ALTER TABLE customers ADD COLUMN z", "CREATE TABLE t (a)",
    "REPLACE INTO customers(id, name) VALUES (1, 'x')", "ATTACH DATABASE 'x.db' AS x", "DETACH x",
    "PRAGMA table_info(customers)", "VACUUM", "WITH x AS (SELECT 1) DELETE FROM customers",
    "SELECT * FROM customers -- trailing", "SELECT /* c */ * FROM customers",
    "SELECT * FROM secrets", "SELECT * FROM sqlite_master", "SELECT name FROM sqlite_schema",
    "SELECT * FROM main.customers", "SELECT * FROM temp.sqlite_master",
    "SELECT * FROM customers UNION SELECT name, type, sql FROM sqlite_master",
    "SELECT password FROM customers", "SELECT c.password FROM customers c", "SELECT x.name FROM customers c",
    "SELECT load_extension('evil')", "SELECT readfile('/etc/passwd')", "SELECT fts3_tokenizer('x')",
    "SELECT randomblob(1000000000)", "SELECT zeroblob(1000000000)", "SELECT printf('%1000000d', 1)",
    "SELECT sqlite_version()", "SELECT * FROM pragma_table_info('customers')",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
    "SELECT * FROM customers WHERE id = ?", "SELECT * FROM customers WHERE id = :x",
    "", "   ", "SELECT 1\x00; DROP TABLE customers", "SELECT " + "1," * 3000 + "1",
]


@pytest.mark.parametrize("sql", MALICIOUS)
def test_malicious_or_invalid_sql_is_rejected(env, sql):
    _, _, schema, _ = env
    with pytest.raises(SqlRejected):
        check_sql(sql, schema)


def test_non_text_input_rejected(env):
    _, _, schema, _ = env
    for bad in (None, 5, b"SELECT 1", ["SELECT 1"]):
        with pytest.raises(SqlRejected):
            check_sql(bad, schema)


def test_rejected_sql_never_changes_the_database(env):
    c, path, schema, _ = env
    before = cat._sha256(path)
    for sql in MALICIOUS:
        try:
            check_sql(sql, schema)
        except SqlRejected:
            pass
    assert cat._sha256(path) == before


# ---- defence in depth: bypass the validator and call the executor directly ----
@pytest.mark.parametrize("sql", [
    "SELECT load_extension('x')", "SELECT name FROM sqlite_master", "SELECT randomblob(10)",
    "INSERT INTO customers(name) VALUES ('x')", "DROP TABLE customers", "CREATE TABLE t (a)",
    "PRAGMA table_info(customers)", "ATTACH DATABASE ':memory:' AS m",
])
def test_executor_blocks_even_unvalidated_sql(env, sql):
    _, path, _, _ = env
    before = cat._sha256(path)
    with pytest.raises(ex.QueryError):
        ex._execute(path, sql, ["customers", "orders", "products", "order_items", "customer_orders"])
    assert cat._sha256(path) == before


def test_executor_rejects_two_statements(env):
    _, path, _, _ = env
    with pytest.raises(ex.QueryError):
        ex._execute(path, "SELECT 1; SELECT 2", ["customers"])


# ---- limits ----
def big_db(tmp_path):
    p = tmp_path / "big.sqlite"
    with closing(sqlite3.connect(p)) as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.executemany("INSERT INTO t(v) VALUES (?)", [("row-%d" % i,) for i in range(2000)])
        con.commit()
    return p, cat.extract_schema(p)


def test_row_cap(tmp_path):
    p, schema = big_db(tmp_path)
    res = ex._execute(p, check_sql("SELECT * FROM t", schema).sql, ["t"], max_rows=100)
    assert len(res.rows) == 100 and res.truncated


def test_result_size_cap(tmp_path):
    p, schema = big_db(tmp_path)
    res = ex._execute(p, check_sql("SELECT * FROM t", schema).sql, ["t"], max_bytes=300)
    assert res.truncated and 0 < len(res.rows) < 100


def test_timeout_stops_an_expensive_query(tmp_path):
    p, schema = big_db(tmp_path)
    sql = check_sql("SELECT COUNT(*) FROM t a, t b, t c WHERE a.v <> b.v", schema).sql
    with pytest.raises(ex.QueryError) as e:
        ex._execute(p, sql, ["t"], timeout_s=0.3)
    assert "timed out" in str(e.value)
    assert ex._execute(p, "SELECT COUNT(*) FROM t", ["t"]).rows == [[2000]]      # database still usable


# ---- stale schema ----
def test_sql_validated_against_old_schema_cannot_run_after_the_database_changes(env):
    c, path, schema, rec = env
    checked = check_sql("SELECT name FROM customers", schema)
    with closing(sqlite3.connect(path)) as con:
        con.execute("ALTER TABLE customers ADD COLUMN email TEXT")
        con.commit()
    os.utime(path, ns=(2000 * 10**9, 2000 * 10**9))
    with pytest.raises(ex.QueryError):
        ex.run_query(c.resolve(SHOP), checked)
    new_schema = c.get_schema(SHOP)
    assert ex.run_query(c.resolve(SHOP), check_sql("SELECT email FROM customers", new_schema)).rows
    with pytest.raises(SqlRejected):
        check_sql("SELECT email FROM customers", schema)       # the old schema does not know the column


def test_run_query_after_removal(env):
    c, path, schema, rec = env
    checked = check_sql("SELECT name FROM customers", schema)
    path.unlink()
    with pytest.raises(ex.QueryError):
        ex.run_query(rec, checked)