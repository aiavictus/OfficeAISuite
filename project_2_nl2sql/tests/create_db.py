
import sqlite3

db = sqlite3.connect("test_schema.sqlite")
db.execute("PRAGMA foreign_keys = ON")

db.executescript("""
DROP VIEW IF EXISTS customer_orders;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    ordered_on TEXT
);

CREATE TABLE order_items (
    order_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    product_id INTEGER REFERENCES products(id),
    qty INTEGER NOT NULL,
    PRIMARY KEY (order_id, line_no),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE INDEX idx_orders_customer
ON orders(customer_id);

CREATE VIEW customer_orders AS
SELECT c.name AS name, COUNT(o.id) AS order_count
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
GROUP BY c.id;

INSERT INTO customers VALUES
(1, 'Asha', 'Pune'),
(2, 'Ben', 'Delhi'),
(3, 'Chen', 'Pune'),
(4, 'Dina', 'Mumbai'),
(5, 'Evan', 'Chennai');

INSERT INTO products VALUES
(1, 'Widget', 20.00),
(2, 'Gadget', 30.00),
(3, 'Gizmo', 50.00);

INSERT INTO orders VALUES
(1, 1, '2025-01-05'),
(2, 2, '2025-02-10'),
(3, 1, '2025-02-11'),
(4, 3, '2025-03-01');

INSERT INTO order_items VALUES
(1, 1, 1, 2),
(1, 2, 2, 1),
(2, 1, 3, 1),
(3, 1, 2, 3),
(4, 1, 1, 5);
""")

db.commit()

# Display schema
for row in db.execute("""
    SELECT type, name, sql
    FROM sqlite_master
    WHERE name NOT LIKE 'sqlite_%'
    ORDER BY type, name
"""):
    print(row)

# Display sample data
print("\\nCustomers:")
for row in db.execute("SELECT * FROM customers"):
    print(row)

print("\\nCustomer order counts:")
for row in db.execute("SELECT * FROM customer_orders"):
    print(row)

db.close()
print("\\nCreated test_schema.sqlite successfully!")
