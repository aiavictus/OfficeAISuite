import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import cleanup, llm_manager, safe_files  # noqa: E402
from shared.safe_files import UploadError  # noqa: E402

MB = 1024 * 1024


def make_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["a", "b"])
    wb.save(path)


def up(tmp_path, data, name, kinds=("pdf", "xlsx", "csv"), limit=MB):
    return safe_files.save_upload(io.BytesIO(data), name, tmp_path / "up", set(kinds), limit)


# ---- filenames and paths ----
@pytest.mark.parametrize("raw", ["..\\..\\evil.csv", "../../evil.csv", "C:\\Windows\\evil.csv", "a/b/evil.csv"])
def test_safe_filename_strips_directories(raw):
    assert safe_files.safe_filename(raw) == "evil.csv"


def test_safe_filename_reserved_and_weird():
    assert safe_files.safe_filename("CON.csv") == "_CON.csv"
    assert safe_files.safe_filename("a<b>:c?.csv") == "a_b__c_.csv"
    assert safe_files.safe_filename("") == "file"


def test_resolve_within_blocks_traversal(tmp_path):
    with pytest.raises(UploadError):
        safe_files.resolve_within(tmp_path, "..\\outside.txt")
    with pytest.raises(UploadError):
        safe_files.resolve_within(tmp_path, "../outside.txt")
    assert safe_files.resolve_within(tmp_path, "ok.txt").parent == tmp_path.resolve()


# ---- upload validation ----
def test_valid_csv_saved_with_random_name(tmp_path):
    r = up(tmp_path, b"a,b\n1,2\n", "..\\..\\data.csv")
    assert r["kind"] == "csv" and r["display_name"] == "data.csv"
    assert r["stored_path"].is_file() and r["stored_path"].name != "data.csv"
    assert not list((tmp_path / "up").glob("*.part"))


def test_valid_xlsx_and_pdf(tmp_path):
    x = tmp_path / "t.xlsx"
    make_xlsx(x)
    assert up(tmp_path, x.read_bytes(), "t.xlsx")["kind"] == "xlsx"
    assert up(tmp_path, b"%PDF-1.4\n%%EOF", "t.pdf")["kind"] == "pdf"


def test_extension_content_mismatch_rejected(tmp_path):
    with pytest.raises(UploadError):
        up(tmp_path, b"MZ\x90\x00binary", "malware.csv")
    with pytest.raises(UploadError):
        up(tmp_path, b"just text", "fake.pdf")
    with pytest.raises(UploadError):
        up(tmp_path, b"a,b\n1,2\n", "fake.xlsx")
    assert not list((tmp_path / "up").glob("*.part"))


def test_fake_zip_not_xlsx(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(UploadError):
        up(tmp_path, buf.getvalue(), "f.xlsx")


def test_unsupported_extension_and_empty_and_oversize(tmp_path):
    with pytest.raises(UploadError):
        up(tmp_path, b"x", "run.exe")
    with pytest.raises(UploadError):
        up(tmp_path, b"", "e.csv")
    with pytest.raises(UploadError):
        up(tmp_path, b"a" * (MB + 1), "big.csv", limit=MB)
    assert not list((tmp_path / "up").glob("*.part"))


def test_kind_not_allowed_for_project(tmp_path):
    with pytest.raises(UploadError):
        up(tmp_path, b"%PDF-1.4", "a.pdf", kinds=("csv", "xlsx"))


# ---- cleanup ----
def test_purge_only_touches_marked_dir(tmp_path):
    owned = cleanup.mark_owned(tmp_path / "tmp")
    (owned / "a.txt").write_text("x")
    (owned / "sub").mkdir()
    (owned / "sub" / "b.txt").write_text("x")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep")
    removed, skipped = cleanup.purge_owned_dir(owned)
    assert removed == 2 and skipped == []
    assert unrelated.exists() and (owned / cleanup.MARKER).exists()
    assert [p.name for p in owned.iterdir()] == [cleanup.MARKER]


def test_purge_refuses_unmarked_dir(tmp_path):
    d = tmp_path / "precious"
    d.mkdir()
    (d / "doc.pdf").write_text("x")
    with pytest.raises(cleanup.CleanupError):
        cleanup.purge_owned_dir(d)
    assert (d / "doc.pdf").exists()


def test_rotate_logs_keeps_newest(tmp_path):
    import os, time
    for i in range(5):
        p = tmp_path / f"{i}.log"
        p.write_text("x")
        os.utime(p, (time.time() - i * 10, time.time() - i * 10))
    (tmp_path / "other.txt").write_text("x")
    assert cleanup.rotate_logs(tmp_path, max_files=2) == 3
    assert sorted(p.name for p in tmp_path.glob("*.log")) == ["0.log", "1.log"]
    assert (tmp_path / "other.txt").exists()


# ---- model registry (no model is started here) ----
def test_registry_defaults_resolve_to_verified_models():
    for role in ("documents", "sql", "dashboard"):
        mid = llm_manager.default_model_id(role)
        assert mid is not None, f"no verified model for role {role}"
        assert mid in [m["id"] for m in llm_manager.verified_models(role)]
    assert "bge-m3" in [m["id"] for m in llm_manager.verified_models(kind="embedding")]


def test_unknown_model_and_profile_raise():
    with pytest.raises(llm_manager.LLMError):
        llm_manager.LlamaServer(profile="nope")
    srv = llm_manager.LlamaServer(port=18999)
    with pytest.raises(llm_manager.LLMError):
        srv.start("does-not-exist")
    with pytest.raises(llm_manager.LLMError):
        srv.chat([{"role": "user", "content": "hi"}])