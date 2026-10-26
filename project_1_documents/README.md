# Project 1: Document Q&A

Start: START_PROJECT_1.bat (opens http://127.0.0.1:8090, local only).
Supported: PDF, XLSX, CSV, PNG, JPG. Max 100 MB each. .xls is not supported.

## How answers are made
1. Only files you tick are read.
2. Numeric questions on tables are calculated with pandas (labelled "Calculated").
3. Otherwise relevant excerpts are retrieved (BM25) and the local model answers with [n] citations.
4. Answers with no citation, a citation that was not supplied, or numbers missing from the cited text are rejected.
5. If nothing relevant is found, the model is not called.

## Where data lives
| Location | Contents | Cleared by |
|---|---|---|
| repository\ | Your saved documents | Only the Delete button |
| data\session_tmp\ | Temporary uploads | Clear session, restart, cleanup |
| data\extracted\ | Extracted text and OCR, named by SHA-256 | Cleanup (stale only, or --all-derived) |
| data\registry.sqlite | File sizes, times, hashes | Never (rebuilt if deleted) |
| logs\ | Model logs | Cleanup keeps 10 files, 14 days |

## Cleanup
CLEANUP_PROJECT_1.bat          temporary uploads, stale derived data, old logs
CLEANUP_PROJECT_1.bat --all-derived   also all extracted text (rebuilt on next question)
Verify: the repository file count printed at the end must not change.
Safe to run while the app is stopped. Locked files are skipped and reported.

## Known limitations
- Cross-file calculations (for example Q1 across a CSV and a PDF) are not computed; models fail or refuse.
- A wrong number taken from the right excerpt can pass validation.
- The calculator ignores lowercase unknown values (for example "doohickey").
- Row ranges in citations run from first to last matching row, not only the rows used.
- Saving the same file twice creates "name (2)".
- Only top-level repository files are scanned.