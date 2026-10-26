# Project 2: Natural language to SQL

Start: START_PROJECT_2.bat (opens http://127.0.0.1:8100, local only).
Supported: SQLite only (.db, .sqlite, .sqlite3), max 500 MB.

## How it works
1. Files in databases\ are discovered on startup and on every request (header bytes, not extension).
2. The schema is read from the database itself and cached by SHA-256. A changed or replaced file gets a fresh schema.
3. The model writes one SELECT. Output is cleaned, parsed with sqlglot and checked against an allowlist and the live schema.
4. "Generate SQL only" never runs anything. "Generate and preview" runs the validated SQL on a read-only connection
   with an authorizer, 5 s timeout, 500-row cap and 1 MB result cap.
5. Generation failures and execution failures are reported separately.

## Automatic vs optional metadata
Automatic: tables, views, columns, types, primary keys, foreign keys.
Optional: schemas\<dbfile>.json with table/column descriptions and extra relationships. Example:
  {"tables": {"customers": {"description": "People who buy", "columns": {"city": "Home city"}}},
   "relationships": [{"from": "orders.customer_id", "to": "customers.id"}]}
Unknown names in this file are ignored with a warning.

## Where data lives
| Location | Contents | Cleared by |
|---|---|---|
| databases\ | Your database files | Never by the app |
| schemas\ | Your optional description files | Never by the app |
| data\schema_cache\ | Extracted schemas named by SHA-256 | Cleanup (stale only) |
| data\upload_tmp\ | Upload staging files | Cleanup |
| logs\ | Model logs | Cleanup keeps 10 files, 14 days |

## Cleanup
CLEANUP_PROJECT_2.bat   Verify: the databases count printed at the end must not change.

## Known limitations
- SQLite only. Window functions, VALUES, GLOB and table functions are not allowed.
- Unqualified columns are checked against all tables in the query, not per scope.
- A valid but wrong query (wrong join, wrong filter) cannot be detected. Review the SQL.
- A database replaced by a file with the same size and modified time is not detected.
- Comments are rejected even inside string literals.
- Tested on small schemas only.	