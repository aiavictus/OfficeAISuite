"""BM25 retrieval over the extracted content of the SELECTED files only.

Chunks are rebuilt per query from the extraction cache, so nothing stale can survive.
Every chunk carries a human-readable citation. BM25 is implemented here (Lucene-style
IDF, always positive) because library versions give zero or negative scores on tiny corpora.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass

ROWS_PER_CHUNK = 25
MAX_CHUNK_CHARS = 2500
TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
STOPWORDS = frozenset(
    "a an the of for in on at to by and or is are was were be been do does did what which who whom "
    "how when where why this that these those it its with from as s per there their".split())
K1, B = 1.5, 0.75


@dataclass(frozen=True)
class Chunk:
    file: str
    kind: str          # "page" | "schema" | "rows"
    cite: str
    text: str


def tokenize(text):
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


def _row_text(row):
    return "; ".join(f"{k}={'' if v is None else v}" for k, v in row.items())


def chunks_from_extracted(data):
    """Turn one extracted-file dict (from extract.extract_file) into chunks."""
    name = data["file"]
    out = []
    if data["kind"] in ("pdf", "image"):
        for p in data["pages"]:
            text = (p["text"] or "").strip()
            if text:
                out.append(Chunk(name, "page", f"{name}, page {p['page']}", text[:MAX_CHUNK_CHARS]))
        return out
    for s in data["sheets"]:
        if s.get("empty"):
            continue
        where = f"{name} / {s['sheet']}" if data["kind"] == "xlsx" else name
        cols = ", ".join(f"{c['name']} ({c['dtype']})" for c in s["columns"])
        out.append(Chunk(name, "schema", f"{where}, header row {s['header_row']}",
                         f"Sheet {s['sheet']} columns: {cols}. {s['row_count']} data rows."))
        rows, src = s["rows"], s["source_rows"]
        for i in range(0, len(rows), ROWS_PER_CHUNK):
            part = rows[i:i + ROWS_PER_CHUNK]
            lo, hi = src[i], src[i + len(part) - 1]
            text = f"Sheet {s['sheet']}\n" + "\n".join(_row_text(r) for r in part)
            out.append(Chunk(name, "rows", f"{where}, rows {lo}-{hi}", text[:MAX_CHUNK_CHARS]))
    return out


def build_chunks(extracted_list):
    chunks = []
    for d in extracted_list:
        chunks.extend(chunks_from_extracted(d))
    return chunks


def search(chunks, question, k=6):
    """Top-k chunks sharing at least one meaningful term with the question. [] if none do."""
    if not chunks:
        return []
    q_terms = set(tokenize(question))
    if not q_terms:
        return []
    docs = [Counter(tokenize(c.text)) for c in chunks]
    lengths = [sum(d.values()) for d in docs]
    n = len(docs)
    avg = (sum(lengths) / n) or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q_terms}
    scores = []
    for d, ln in zip(docs, lengths):
        s = 0.0
        for t in q_terms:
            f = d.get(t, 0)
            if f == 0:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * ln / avg))
        scores.append(s)
    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
    return [(chunks[i], scores[i]) for i in ranked[:k] if scores[i] > 0]