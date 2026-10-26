"""Project 2 locations and limits. All paths derive from this file's position."""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]      # ...\project_2_nl2sql
DB_DIR = PROJECT_DIR / "databases"                      # approved database files (never auto-deleted)
SCHEMA_DIR = PROJECT_DIR / "schemas"                    # optional <dbfile>.json description files
DATA_DIR = PROJECT_DIR / "data"
SCHEMA_CACHE_DIR = DATA_DIR / "schema_cache"            # extracted schemas, named by SHA-256
LOG_DIR = PROJECT_DIR / "logs"

DB_EXTENSIONS = (".db", ".sqlite", ".sqlite3")          # SQLite only
MAX_DB_BYTES = 500 * 1024 * 1024
MAX_TABLES = 300
MAX_COLUMNS_PER_TABLE = 300
MAX_SIDECAR_BYTES = 1024 * 1024