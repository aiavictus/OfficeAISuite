"""Deletes only application-owned folders (those carrying a marker file)."""
import os
import shutil
import time
from pathlib import Path

MARKER = ".suite_owned"


class CleanupError(RuntimeError):
    pass


def mark_owned(directory) -> Path:
    """Create the directory (if needed) and tag it as safe for the app to purge."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    (d / MARKER).write_text("Application-owned temporary folder. Contents may be deleted.\n", encoding="ascii")
    return d


def purge_owned_dir(directory, older_than_seconds=None):
    """Delete the CONTENTS of a marked folder. Returns (removed_count, skipped_paths).

    Refuses any folder without the marker. Never follows symlinks.
    Files that are locked are skipped and reported, not forced.
    """
    d = Path(directory)
    if not (d / MARKER).is_file():
        raise CleanupError(f"Refusing to clean '{d}': it is not marked as application-owned.")
    removed, skipped = 0, []
    cutoff = None if older_than_seconds is None else time.time() - older_than_seconds
    for entry in os.scandir(d):
        if entry.name == MARKER:
            continue
        p = Path(entry.path)
        try:
            if cutoff is not None and entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
            if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                p.unlink()
            else:
                shutil.rmtree(p)
            removed += 1
        except OSError:
            skipped.append(str(p))
    return removed, skipped


def rotate_logs(log_dir, max_files=10, max_age_days=14):
    """Keep at most max_files .log files and drop those older than max_age_days."""
    d = Path(log_dir)
    if not d.is_dir():
        return 0
    logs = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for i, p in enumerate(logs):
        if i >= max_files or p.stat().st_mtime < cutoff:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed