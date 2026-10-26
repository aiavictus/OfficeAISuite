"""Project 1 locations and limits. All paths derive from this file's position."""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]     # ...\project_1_documents
REPO_DIR = PROJECT_DIR / "repository"                  # permanent files (never auto-deleted)
DATA_DIR = PROJECT_DIR / "data"
SESSION_DIR = DATA_DIR / "session_tmp"                 # temporary uploads, one sub-folder per session
EXTRACTED_DIR = DATA_DIR / "extracted"                 # derived text/tables, keyed by file SHA-256
INDEX_DIR = DATA_DIR / "index"
REGISTRY_DB = DATA_DIR / "registry.sqlite"
LOG_DIR = PROJECT_DIR / "logs"

ALLOWED_KINDS = {"pdf", "xlsx", "csv", "png", "jpg"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024