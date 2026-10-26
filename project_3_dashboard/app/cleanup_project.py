"""Project 3 cleanup. Removes only the dashboard's temporary workspace and old logs.
  python project_3_dashboard\\app\\cleanup_project.py
Nothing outside workspace\\active is ever touched. Your original spreadsheets are never stored here."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parent))

from shared.cleanup import mark_owned, purge_owned_dir, rotate_logs  # noqa: E402

from dashapp import config  # noqa: E402


def main():
    removed, skipped = purge_owned_dir(mark_owned(config.WORKSPACE_DIR))
    print(f"Temporary uploads removed: {removed}" + (f" (locked, skipped: {len(skipped)})" if skipped else ""))
    print(f"Old logs removed: {rotate_logs(config.LOG_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())