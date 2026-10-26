"""Upload validation by CONTENT, safe filenames, and path-traversal protection."""
import re
import uuid
import zipfile
from pathlib import Path

EXT_TO_KIND = {".pdf": "pdf", ".xlsx": "xlsx", ".csv": "csv",
               ".png": "png", ".jpg": "jpg", ".jpeg": "jpg"}
MAX_UNZIPPED_BYTES = 300 * 1024 * 1024   # zip-bomb guard for .xlsx


class UploadError(ValueError):
    """Message is safe to show to the user."""


def safe_filename(name: str, max_len: int = 100) -> str:
    """Drop any directory part, replace unsafe characters, block reserved Windows names."""
    name = (name or "").replace("\\", "/").split("/")[-1].strip().strip(".")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", stem).strip(" .") or "file"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10))}:
        stem = "_" + stem
    stem = stem[:max_len]
    return f"{stem}.{ext}" if ext else stem


def resolve_within(base: Path, name: str) -> Path:
    """Resolve name under base. Raises if the result would leave base."""
    base = Path(base).resolve()
    target = (base / name).resolve()
    if base != target and base not in target.parents:
        raise UploadError("Invalid file path.")
    return target


def sniff_kind(path: Path):
    """Return pdf | xlsx | csv | png | jpg, judged from file CONTENT, or None."""
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(65536)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
                    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
                        return None
                    return "xlsx"
        except zipfile.BadZipFile:
            return None
        return None
    if head and b"\x00" not in head:
        ctrl = sum(1 for b in head if b < 32 and b not in (9, 10, 13))
        if ctrl / len(head) < 0.01:
            return "csv"
    return None


def save_upload(fileobj, original_name, dest_dir, allowed_kinds, max_bytes):
    """Stream fileobj (anything with .read(n)) into dest_dir under a random name.

    Validates size, extension AND content. Returns a dict with safe metadata.
    Raises UploadError (safe to show) and leaves no partial file behind.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean = safe_filename(original_name)
    ext = Path(clean).suffix.lower()
    expected = EXT_TO_KIND.get(ext)
    if expected is None or expected not in allowed_kinds:
        allowed = ", ".join(sorted(e for e, k in EXT_TO_KIND.items() if k in allowed_kinds))
        raise UploadError(f"Unsupported file type '{ext or 'none'}'. Allowed: {allowed}")

    file_id = uuid.uuid4().hex
    part = resolve_within(dest_dir, file_id + ".part")
    total = 0
    try:
        with open(part, "wb") as out:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadError(f"File too large (limit {max_bytes // (1024 * 1024)} MB).")
                out.write(chunk)
        if total == 0:
            raise UploadError("The file is empty.")
        kind = sniff_kind(part)
        if kind != expected:
            raise UploadError(f"The content of '{clean}' does not match a valid {expected.upper()} file.")
        final = resolve_within(dest_dir, f"{file_id}{ext}")
        part.replace(final)
        return {"id": file_id, "display_name": clean, "stored_path": final, "kind": kind, "size": total}
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise