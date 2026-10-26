"""Report OCR quality for a real scan.
    python project_1_documents\\tests\\ocr_check.py <file.pdf|png|jpg> [--dpi 200]
Compares the app's page mode (psm 6) with automatic layout (psm 3) and lists low-confidence words."""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "application"))
from shared.paths import TESSERACT_DIR  # noqa: E402

EXE = TESSERACT_DIR / "tesseract.exe"


def words(img, psm):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "p.png"
        img.convert("L").save(p)
        env = dict(os.environ, TESSDATA_PREFIX=str(TESSERACT_DIR / "tessdata"))
        r = subprocess.run([str(EXE), str(p), "stdout", "-l", "eng", "--psm", str(psm), "tsv"],
                           capture_output=True, env=env, cwd=td, timeout=180)
    out = []
    for line in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
        c = line.split("\t")
        if len(c) == 12 and c[11].strip() and float(c[10]) >= 0:
            out.append((c[11], float(c[10])))
    return out


def pages(path, dpi):
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        for i in range(len(pdf)):
            yield i + 1, pdf[i].render(scale=dpi / 72).to_pil()
    else:
        from PIL import Image
        yield 1, Image.open(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--dpi", type=int, default=200)
    a = ap.parse_args()
    for n, img in pages(Path(a.file), a.dpi):
        print(f"\n--- page {n} ({img.size[0]}x{img.size[1]}) ---")
        for psm in (6, 3):
            w = words(img, psm)
            if not w:
                print(f"psm {psm}: no words found")
                continue
            conf = sum(c for _, c in w) / len(w)
            low = [x for x in w if x[1] < 60]
            print(f"psm {psm}: {len(w)} words, mean confidence {conf:.0f}, {len(low)} below 60")
            if psm == 6:
                nums = [f"{t}({c:.0f})" for t, c in w if re.search(r"\d", t) and c < 80]
                print("  numbers to double-check:", ", ".join(nums[:20]) or "none")
                print("  lowest words:", ", ".join(f"{t}({c:.0f})" for t, c in sorted(w, key=lambda x: x[1])[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())