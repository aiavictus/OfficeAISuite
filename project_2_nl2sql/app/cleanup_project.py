"""Project 2 cleanup. Removes only application-owned temporary data.

  python project_2_nl2sql\\app\\cleanup_project.py

Removes: stale cached schemas (databases no longer present in that exact form),
         leftover upload staging files, old logs.
NEVER touched: databases\\ (your database files), schemas\\ (your description files), models.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parent))

from shared.cleanup import mark_owned, purge_owned_dir, rotate_logs  # noqa: E402

from docsql import config  # noqa: E402
from docsql.catalog import DatabaseCatalog  # noqa: E402


def main():
    cat = DatabaseCatalog(config.DB_DIR, config.SCHEMA_CACHE_DIR, config.SCHEMA_DIR)
    print(f"Stale cached schemas removed: {cat.prune()}")
    removed, skipped = purge_owned_dir(mark_owned(config.DATA_DIR / "upload_tmp"))
    print(f"Upload staging files removed: {removed}" + (f" (locked, skipped: {len(skipped)})" if skipped else ""))
    print(f"Old logs removed: {rotate_logs(config.LOG_DIR)}")
    print(f"Databases untouched: {len(list(config.DB_DIR.glob('*')))} item(s) in {config.DB_DIR.name}\\")
    return 0


if __name__ == "__main__":
    sys.exit(main())