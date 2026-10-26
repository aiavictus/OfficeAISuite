"""Project 1 cleanup. Removes only application-owned temporary data.

  python project_1_documents\\app\\cleanup_project.py            session uploads + stale derived data + old logs
  python project_1_documents\\app\\cleanup_project.py --all-derived   also clears ALL extracted text (rebuilt on demand)

NEVER touched: repository\\ (your documents), models, the registry database.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parent))

from shared.cleanup import mark_owned, purge_owned_dir, rotate_logs  # noqa: E402

from docqa import config  # noqa: E402
from docqa.registry import FileRegistry, prune_derived  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-derived", action="store_true")
    a = ap.parse_args()

    removed, skipped = purge_owned_dir(mark_owned(config.SESSION_DIR))
    print(f"Session uploads removed: {removed}" + (f" (locked, skipped: {len(skipped)})" if skipped else ""))

    ext = mark_owned(config.EXTRACTED_DIR)
    if a.all_derived:
        n, sk = purge_owned_dir(ext)
        print(f"Extracted data removed: {n}" + (f" (skipped: {len(sk)})" if sk else ""))
    else:
        reg = FileRegistry(config.REPO_DIR, config.REGISTRY_DB, config.ALLOWED_KINDS)
        try:
            live = {r.sha256 for r in reg.scan().records}
        finally:
            reg.close()
        print(f"Stale extracted data removed: {prune_derived(ext, live)}")

    print(f"Old logs removed: {rotate_logs(config.LOG_DIR)}")
    print(f"Repository untouched: {len(list(config.REPO_DIR.glob('*')))} item(s) still in {config.REPO_DIR.name}\\")
    return 0


if __name__ == "__main__":
    sys.exit(main())