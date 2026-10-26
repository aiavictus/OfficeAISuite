"""Build the deterministic sample database used by Project 2 tests.
    python project_2_nl2sql\\tests\\make_sample_db.py
Writes project_2_nl2sql\\tests\\fixtures\\shop.sqlite
"""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures"

DDL = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers, ordered_on TEXT);
CREATE TABLE order_items (
    order_id INTEGER NOT NULL, line_no INTEGER NOT NULL, product_id INTEGER, qty INTEGER,
    PRIMARY KEY (order_id, line_no),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (order_id) REFERENCES orders(id));
CREATE VIEW customer_orders AS
    SELECT c.name AS name, COUNT(o.id) AS order_count
    FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id;
"""

CUSTOMERS = [(1, "Asha", "Pune"), (2, "Ben", "Delhi"), (3, "Chen", "Pune"), (4, "Dina", "Mumbai")]
PRODUCTS = [(1, "Widget", 20.0), (2, "Gadget", 30.0), (3, "Gizmo", 15.5)]
ORDERS = [(1, 1, "2025-01-05"), (2, 1, "2025-02-10"), (3, 2, "2025-02-11"), (4, 3, "2025-03-01")]
ITEMS = [(1, 1, 1, 2), (1, 2, 2, 1), (2, 1, 3, 4), (3, 1, 1, 5), (4, 1, 2, 2)]


def build(path):
    path = Path(path)
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as c:
        c.executescript(DDL)
        c.executemany("INSERT INTO customers VALUES (?,?,?)", CUSTOMERS)
        c.executemany("INSERT INTO products VALUES (?,?,?)", PRODUCTS)
        c.executemany("INSERT INTO orders VALUES (?,?,?)", ORDERS)
        c.executemany("INSERT INTO order_items VALUES (?,?,?,?)", ITEMS)
        c.commit()
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    print("Written:", build(OUT / "shop.sqlite"))
    sys.exit(0)