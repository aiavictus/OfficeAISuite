import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))
sys.path.insert(0, str(HERE.parent))

import make_dash_fixtures as fx  # noqa: E402
from dashapp import config  # noqa: E402
from dashapp.profile import DataError  # noqa: E402
from dashapp.workspace import BatchError, Workspace, WorkspaceError  # noqa: E402
from shared.cleanup import MARKER  # noqa: E402
from shared.safe_files import UploadError  # noqa: E402


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws" / "active"
    (tmp_path / "keep.txt").write_text("keep")
    return Workspace(root), root, tmp_path, fx.build_all(tmp_path / "fx")


def up(ws, batch, path, name=None):
    return ws.add_upload(batch, io.BytesIO(Path(path).read_bytes()), name or Path(path).name)


def stored(root):
    return [p for p in root.rglob("*") if p.is_file() and p.name != MARKER]


def test_valid_uploads_are_profiled_and_listed_without_paths(env):
    ws, root, _, f = env
    b = ws.new_batch()
    up(ws, b, f["orders"])
    up(ws, b, f["store_b"])
    listing = ws.files(b)
    assert sorted(x["name"] for x in listing) == ["orders_2025.xlsx", "store_b.csv"]
    orders = next(x for x in listing if x["kind"] == "xlsx")
    assert [t["sheet"] for t in orders["tables"]] == ["Orders", "Notes"] and orders["tables"][0]["rows"] == 40
    assert orders["skipped_sheets"] == ["Blank"] and str(root) not in json.dumps(listing)


@pytest.mark.parametrize("data,name", [(b"a,b\n1,2\n", "fake.xlsx"), (b"%PDF-1.4\n%%EOF", "x.pdf"),
                                       (b"MZ\x00\x01", "evil.csv"), (b"x", "run.exe"), (b"", "empty.csv")])
def test_invalid_uploads_rejected_and_leave_nothing(env, data, name):
    ws, root, _, _ = env
    b = ws.new_batch()
    with pytest.raises(UploadError):
        ws.add_upload(b, io.BytesIO(data), name)
    assert ws.files(b) == [] and stored(root) == []


def test_unparseable_but_valid_looking_xlsx_is_removed(env):
    ws, root, _, _ = env
    b = ws.new_batch()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "x")
        z.writestr("xl/workbook.xml", "not xml")
    with pytest.raises(DataError):
        ws.add_upload(b, io.BytesIO(buf.getvalue()), "broken.xlsx")
    assert ws.files(b) == [] and stored(root) == []


def test_header_only_csv_rejected_and_removed(env):
    ws, root, _, _ = env
    b = ws.new_batch()
    with pytest.raises(DataError):
        ws.add_upload(b, io.BytesIO(b"a,b\n"), "h.csv")
    assert ws.files(b) == [] and stored(root) == []


def test_traversal_filename_stays_inside_workspace(env):
    ws, root, tmp, _ = env
    b = ws.new_batch()
    ds = ws.add_upload(b, io.BytesIO(b"a,b\n1,2\n"), "..\\..\\evil.csv")
    assert ds.name == "evil.csv" and not (tmp / "evil.csv").exists() and not (tmp / "ws" / "evil.csv").exists()
    assert all(root in p.parents for p in stored(root))


def test_new_batch_replaces_everything_and_old_ids_stop_working(env):
    ws, root, tmp, f = env
    b1 = ws.new_batch()
    old = up(ws, b1, f["orders"])
    assert len(ws.get_table(b1, old.file_id, "Orders")) == 40
    b2 = ws.new_batch()
    assert b2 != b1 and ws.files(b2) == [] and stored(root) == []
    for call in (lambda: ws.files(b1), lambda: ws.get_table(b1, old.file_id, "Orders"),
                 lambda: up(ws, b1, f["store_b"])):
        with pytest.raises(BatchError):
            call()
    with pytest.raises(BatchError):                       # old file id on the NEW batch
        ws.get_table(b2, old.file_id, "Orders")
    new = up(ws, b2, f["store_b"])
    assert new.file_id != old.file_id and [x["name"] for x in ws.files(b2)] == ["store_b.csv"]
    assert (tmp / "keep.txt").read_text() == "keep" and (f["orders"]).is_file()


@pytest.mark.parametrize("bad", ["../x", "", None, "a" * 32, 5, "..\\.."])
def test_bad_batch_ids_rejected(env, bad):
    ws, _, _, _ = env
    ws.new_batch()
    with pytest.raises(BatchError):
        ws.files(bad)


def test_no_batch_before_first_upload(env):
    ws, _, _, _ = env
    with pytest.raises(BatchError):
        ws.files("a" * 32)


def test_clear_removes_only_workspace_files(env):
    ws, root, tmp, f = env
    b = ws.new_batch()
    up(ws, b, f["messy"])
    ws.clear()
    assert stored(root) == [] and (root / MARKER).is_file() and (tmp / "keep.txt").exists()
    with pytest.raises(BatchError):
        ws.files(b)


def test_restart_clears_leftovers_and_old_batch_is_dead(env):
    ws, root, tmp, f = env
    b = ws.new_batch()
    up(ws, b, f["messy"])
    ws2 = Workspace(root)
    assert stored(root) == [] and (tmp / "keep.txt").exists()
    with pytest.raises(BatchError):
        ws2.files(b)


def test_refuses_unmarked_non_empty_folder(tmp_path):
    d = tmp_path / "userdocs"
    d.mkdir()
    (d / "important.xlsx").write_text("precious")
    with pytest.raises(WorkspaceError):
        Workspace(d)
    assert (d / "important.xlsx").read_text() == "precious"


def test_batch_started_during_upload_discards_the_file(env):
    ws, root, _, f = env
    b = ws.new_batch()

    class Racy(io.BytesIO):
        fired = False

        def read(self, n=-1):
            if not Racy.fired:
                Racy.fired = True
                ws.new_batch()
            return super().read(n)

    with pytest.raises(BatchError):
        ws.add_upload(b, Racy(Path(f["store_b"]).read_bytes()), "store_b.csv")
    b2 = ws.new_batch()
    assert ws.files(b2) == []


def test_file_count_and_size_limits(env, monkeypatch):
    ws, _, _, f = env
    b = ws.new_batch()
    monkeypatch.setattr(config, "MAX_FILES", 2)
    up(ws, b, f["store_b"])
    up(ws, b, f["messy"])
    with pytest.raises(BatchError):
        up(ws, b, f["orders"])
    monkeypatch.undo()
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(UploadError):
        up(ws, b, f["orders"])


def test_get_table_returns_a_copy(env):
    ws, _, _, f = env
    b = ws.new_batch()
    ds = up(ws, b, f["store_b"])
    df = ws.get_table(b, ds.file_id, "data")
    df["Units"] = -1
    assert (ws.get_table(b, ds.file_id, "data")["Units"] > 0).all()
    with pytest.raises(BatchError):
        ws.get_table(b, ds.file_id, "NoSuchSheet")