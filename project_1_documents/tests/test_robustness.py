import hashlib
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
sys.path.insert(0, str(HERE.parents[1] / "app"))

from docqa import calc, extract as ex  # noqa: E402
from docqa.registry import FileRecord  # noqa: E402

FIX = HERE.parent / "fixtures"
DF = pd.read_csv(FIX / "sales_2025.csv")


@pytest.fixture(scope="module")
def csv_data(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r")
    dst = tmp / "sales_2025.csv"
    shutil.copyfile(FIX / "sales_2025.csv", dst)
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()
    r = FileRecord("repo:x", "sales_2025.csv", "csv", dst.stat().st_size, 0, sha, 1, dst, "repository")
    return [ex.extract_file(r, tmp / "cache")]


@pytest.mark.parametrize("question,expected", [
    ("What is the sum of revenue for South?", DF[DF.Region == "South"].Revenue.sum()),
    ("Total units for Gadget", DF[DF.Product == "Gadget"].Units.sum()),
    ("What is the highest revenue?", DF.Revenue.max()),
    ("Average units for Widget in the North region", DF[(DF.Product == "Widget") & (DF.Region == "North")].Units.mean()),
    ("How many rows are in the South region?", float((DF.Region == "South").sum())),
])
def test_unseen_phrasings_compute_correctly(csv_data, question, expected):
    res = calc.try_calculate(question, csv_data)
    assert res is not None, question
    assert res.value == pytest.approx(float(expected))


@pytest.mark.parametrize("question", [
    "What is the total revenue in July 2025?",           # no such month
    "What is the total revenue for the West region?",    # no such region
    "What is the average units for Doohickey?",          # no such product
])
def test_values_that_do_not_exist_must_not_give_a_number(csv_data, question):
    assert calc.try_calculate(question, csv_data) is None, question

@pytest.mark.parametrize("question,expected", [
    ("What is the total revenue for North in 2025-03?", DF[(DF.Region == "North") & (DF.Month == "2025-03")].Revenue.sum()),
    ("Total revenue for Gizmo", DF[DF.Product == "Gizmo"].Revenue.sum()),
])
def test_guard_does_not_block_real_values(csv_data, question, expected):
    res = calc.try_calculate(question, csv_data)
    assert res is not None, question
    assert res.value == pytest.approx(float(expected))