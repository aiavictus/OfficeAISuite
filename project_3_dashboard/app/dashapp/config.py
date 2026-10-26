"""Project 3 locations and limits. All paths derive from this file's position."""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]      # ...\project_3_dashboard
WORKSPACE_DIR = PROJECT_DIR / "workspace" / "active"   # temporary uploads only; replaced per batch
LOG_DIR = PROJECT_DIR / "logs"

ALLOWED_KINDS = {"xlsx", "csv"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FILES = 10
MAX_SHEETS = 20
MAX_ROWS = 200_000
MAX_COLUMNS = 100
MAX_COMPARE = 4              # soft limit on files in one comparison
MAX_RESULT_ROWS = 1000
TABLE_PREVIEW_ROWS = 200