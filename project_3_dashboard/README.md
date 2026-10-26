# Project 3: Spreadsheet dashboard

Start: START_PROJECT_3.bat (opens http://127.0.0.1:8110, local only).
Supported: XLSX and CSV. Max 10 files, 50 MB each, 200,000 rows and 100 columns per sheet.

## How it works
1. Uploads are checked by content and parsed into a temporary workspace (workspace\active\).
2. Each sheet is profiled: column kinds, empty values, duplicates, mixed types.
3. Ready-made views are generated from column kinds with no model.
4. Your own choices (dropdowns) or a typed request become a JSON plan.
   For typed requests a local model proposes the plan from column names and sample values only.
5. Every plan is validated against the real columns and values, then pandas computes the result.
   The model never calculates a number and no generated code is run.
6. Each result shows the description, the metrics, a chart and the data behind it.
7. Comparison runs one plan on 2 to 4 sheets with matching columns and shows one series per file.

## Retention
Nothing is saved. Starting a new batch, pressing Clear all, restarting, or running
CLEANUP_PROJECT_3.bat removes the temporary files. Your original files are never touched.

## Known limitations
- A valid plan can still answer a different question than you meant. Check the "Plan used" line.
- Typed requests work on one sheet; comparison uses ready-made views.
- Formulas never calculated by Excel read as empty. Merged headers become column_N.
- Slash dates are read month-first. "1,200" stored as text stays text.
- Date "between" filters with a plain end date exclude later times that day.
- Tested on small files only.