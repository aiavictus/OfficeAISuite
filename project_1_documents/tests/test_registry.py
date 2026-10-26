import io
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docqa import registry as reg_mod  # noqa: E402
from docqa.registry import FileRegistry, SelectionError, prune_derived  # noqa: E402
from docqa.sessions import SessionError, SessionStore, resolve_selection  # noqa: E402
from shared import cleanup  # noqa: E402
from shared.safe_files import UploadError  # noqa: E402

KINDS = {"pdf", "xlsx", "csv", "png", "jpg"}
CSV1 = b"a,b\n1,2\n"
CSV2 = b"a,b\n1,3\n"       # same size as CSV1, different content
PDF = b"%PDF-1.4\n%%EOF\n"


def put(path, data, t):
    path.write_bytes(data)
    os.utime(path, ns=(t * 10**9, t * 10**9))


def xlsx_bytes():
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["x", "y"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    reg = FileRegistry(repo, tmp_path / "data" / "reg.sqlite", KINDS)
    yield repo, reg, tmp_path
    reg.close()


def names(records):
    return [r.name for r in records]


# ---- discovery ----
def test_new_files_discovered_and_junk_ignored(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    put(repo / "b.pdf", PDF, 1000)
    put(repo / "c.xlsx", xlsx_bytes(), 1000)
    put(repo / "notes.txt", b"hello", 1000)           # unsupported type
    put(repo / "half.csv.part", CSV1, 1000)           # partial upload
    put(repo / ".hidden.csv", CSV1, 1000)
    (repo / "sub").mkdir()
    put(repo / "sub" / "deep.csv", CSV1, 1000)        # sub-folders ignored
    r = reg.scan()
    assert names(r.records) == ["a.csv", "b.pdf", "c.xlsx"]
    assert names(r.new) == ["a.csv", "b.pdf", "c.xlsx"]
    assert all(x.version == 1 and len(x.sha256) == 64 for x in r.records)


def test_unchanged_files_are_not_rehashed(env, monkeypatch):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    reg.scan()
    calls = []
    real = reg_mod._sha256
    monkeypatch.setattr(reg_mod, "_sha256", lambda p: calls.append(p) or real(p))
    r = reg.scan()
    assert calls == [] and names(r.unchanged) == ["a.csv"] and not r.new and not r.modified


def test_modified_same_size_detected_by_hash(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    first = reg.scan().records[0]
    put(repo / "a.csv", CSV2, 2000)
    r = reg.scan()
    assert names(r.modified) == ["a.csv"]
    assert r.records[0].version == 2 and r.records[0].sha256 != first.sha256


def test_touched_only_is_not_a_change(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    reg.scan()
    put(repo / "a.csv", CSV1, 5000)                   # new mtime, same content
    r = reg.scan()
    assert not r.modified and r.records[0].version == 1


def test_replaced_file_between_scans_detected(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    reg.scan()
    (repo / "a.csv").unlink()
    put(repo / "a.csv", b"a,b\n9,9\n7,7\n", 3000)
    r = reg.scan()
    assert names(r.modified) == ["a.csv"] and r.records[0].version == 2


def test_removed_file_disappears_and_cannot_be_selected(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    put(repo / "b.csv", CSV2, 1000)
    reg.scan()
    (repo / "a.csv").unlink()
    r = reg.scan()
    assert r.removed == ["a.csv"] and names(r.records) == ["b.csv"]
    with pytest.raises(SelectionError):
        reg.resolve(["repo:a.csv"])


def test_invalid_content_is_reported_not_usable(env):
    repo, reg, _ = env
    put(repo / "fake.pdf", CSV1, 1000)
    put(repo / "fake.xlsx", CSV1, 1000)
    r = reg.scan()
    assert sorted(r.invalid) == ["fake.pdf", "fake.xlsx"] and r.records == []


def test_state_survives_restart_and_offline_edits_are_detected(env):
    repo, reg, tmp = env
    put(repo / "a.csv", CSV1, 1000)
    put(repo / "b.csv", CSV1, 1000)
    reg.scan()
    reg.close()
    put(repo / "b.csv", CSV2, 2000)                   # edited while the app was stopped
    put(repo / "c.csv", CSV1, 2000)                   # added while the app was stopped
    reg2 = FileRegistry(repo, tmp / "data" / "reg.sqlite", KINDS)
    try:
        r = reg2.scan()
        assert names(r.unchanged) == ["a.csv"] and names(r.modified) == ["b.csv"] and names(r.new) == ["c.csv"]
    finally:
        reg2.close()


# ---- selection: only selected files may be used ----
def test_resolve_returns_only_requested_files(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    put(repo / "b.csv", CSV2, 1000)
    assert names(reg.resolve(["repo:a.csv"])) == ["a.csv"]
    assert names(reg.resolve(["repo:a.csv", "repo:a.csv"])) == ["a.csv"]


@pytest.mark.parametrize("bad", [["repo:..\\..\\x.csv"], ["repo:../x.csv"], ["C:\\Windows\\win.ini"],
                                 [5], [], "repo:a.csv", None])
def test_resolve_rejects_bad_ids(env, bad):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    with pytest.raises(SelectionError):
        reg.resolve(bad)


def test_selection_limit(env):
    _, reg, _ = env
    with pytest.raises(SelectionError):
        reg.resolve([f"repo:{i}.csv" for i in range(reg_mod.MAX_SELECTION + 1)])


# ---- derived data ----
def test_prune_derived_removes_only_stale_entries(tmp_path):
    d = cleanup.mark_owned(tmp_path / "extracted")
    (d / "aaa.json").write_text("{}")
    (d / "bbb.json").write_text("{}")
    (d / "bbb.tables.json").write_text("{}")
    assert prune_derived(d, {"aaa"}) == 2
    assert sorted(p.name for p in d.iterdir()) == [cleanup.MARKER, "aaa.json"]


def test_prune_derived_refuses_unmarked_folder(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "x.json").write_text("{}")
    with pytest.raises(cleanup.CleanupError):
        prune_derived(d, set())
    assert (d / "x.json").exists()


# ---- explicit save and delete ----
def test_save_copy_never_overwrites_and_sanitises(env):
    repo, reg, tmp = env
    src = tmp / "src.csv"
    src.write_bytes(CSV1)
    n1 = reg.save_copy(src, "..\\..\\report.csv")
    n2 = reg.save_copy(src, "report.csv")
    assert n1 == "report.csv" and n2 == "report (2).csv"
    assert not list(repo.glob("*.part"))
    assert names(reg.scan().records) == ["report (2).csv", "report.csv"]


def test_delete_removes_only_the_chosen_repository_file(env):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    put(repo / "b.csv", CSV2, 1000)
    put(repo / "notes.txt", b"keep me", 1000)
    reg.scan()
    assert reg.delete("repo:a.csv") == "a.csv"
    assert names(reg.scan().records) == ["b.csv"] and (repo / "notes.txt").exists()
    with pytest.raises(SelectionError):
        reg.delete("repo:nope.csv")
    with pytest.raises(SelectionError):
        reg.delete("repo:..\\notes.txt")


# ---- sessions ----
@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sess", {"csv", "xlsx", "pdf"}, 1024 * 1024)


def test_upload_validated_and_listed(store):
    sid = store.new_session()
    rec = store.add_upload(sid, io.BytesIO(CSV1), "..\\..\\x.csv")
    assert rec.file_id.startswith("tmp:") and rec.name == "x.csv" and rec.source == "session"
    assert [r.name for r in store.list_files(sid)] == ["x.csv"]
    with pytest.raises(UploadError):
        store.add_upload(sid, io.BytesIO(b"MZ\x00\x01"), "evil.csv")
    with pytest.raises(UploadError):
        store.add_upload(sid, io.BytesIO(b"x"), "run.exe")
    assert len(store.list_files(sid)) == 1


@pytest.mark.parametrize("sid", ["../x", "..", "", None, "a" * 32, 5])
def test_bad_session_ids_rejected(store, sid):
    with pytest.raises(SessionError):
        store.list_files(sid)


def test_sessions_are_isolated(store):
    s1, s2 = store.new_session(), store.new_session()
    rec = store.add_upload(s1, io.BytesIO(CSV1), "x.csv")
    with pytest.raises(SelectionError):
        store.resolve(s2, [rec.file_id])


def test_clear_session_keeps_repository_files(env, store):
    repo, reg, tmp = env
    put(repo / "keep.csv", CSV1, 1000)
    sid = store.new_session()
    store.add_upload(sid, io.BytesIO(CSV2), "temp.csv")
    store.clear(sid)
    assert not (tmp / "sess" / sid).exists() and (repo / "keep.csv").exists()
    with pytest.raises(SessionError):
        store.list_files(sid)


def test_save_to_repository_is_explicit_and_keeps_session_copy(env, store):
    repo, reg, _ = env
    sid = store.new_session()
    rec = store.add_upload(sid, io.BytesIO(CSV1), "x.csv")
    assert reg.scan().records == []                    # uploading alone does not save
    name = store.save_to_repository(sid, rec.file_id, reg)
    assert name == "x.csv" and names(reg.scan().records) == ["x.csv"]
    assert [r.name for r in store.list_files(sid)] == ["x.csv"]


def test_purge_all_clears_leftovers_only_in_session_root(store, tmp_path):
    sid = store.new_session()
    store.add_upload(sid, io.BytesIO(CSV1), "x.csv")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    store.purge_all()
    assert outside.exists() and not (tmp_path / "sess" / sid).exists()
    assert (tmp_path / "sess" / cleanup.MARKER).exists()


# ---- combined selection ----
def test_resolve_selection_mixes_sources_and_only_returns_selected(env, store):
    repo, reg, _ = env
    put(repo / "a.csv", CSV1, 1000)
    put(repo / "b.csv", CSV2, 1000)
    sid = store.new_session()
    t1 = store.add_upload(sid, io.BytesIO(CSV1), "t1.csv")
    store.add_upload(sid, io.BytesIO(CSV2), "t2.csv")
    got = resolve_selection(reg, store, sid, ["repo:a.csv", t1.file_id])
    assert sorted(r.name for r in got) == ["a.csv", "t1.csv"]


@pytest.mark.parametrize("bad", [["../x"], ["etc:passwd"], []])
def test_resolve_selection_rejects_unknown_prefixes(env, store, bad):
    _, reg, _ = env
    with pytest.raises(SelectionError):
        resolve_selection(reg, store, store.new_session(), bad)