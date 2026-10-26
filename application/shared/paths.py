"""Suite-relative locations. Everything derives from this file's own position."""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]      # ...\OfflineAISuite\application
SUITE_ROOT = APP_DIR.parent                        # ...\OfflineAISuite
MODELS_DIR = APP_DIR / "models"
MODELS_JSON = MODELS_DIR / "models.json"
PROFILES_JSON = APP_DIR / "shared" / "config" / "llm_profiles.json"
LLAMA_SERVER = APP_DIR / "llm_runtime" / "llama.cpp" / "llama-server.exe"
TESSERACT_DIR = APP_DIR / "ocr" / "tesseract"
STATIC_DIR = APP_DIR / "shared" / "static"
APP_LOG_DIR = APP_DIR / "logs"