# OfflineAISuite

Three local, offline AI web apps for Windows that share one portable runtime.
Everything runs from this folder. No installer, no administrator rights, no cloud services.

## Status

| Part | State |
|---|---|
| application/ (Python 3.11 embeddable, packages, llama.cpp, Tesseract, models, shared code, verifier) | Built, checks pass on the preparation PC |
| project_1_documents (PDF / Excel / CSV question answering) | Not built yet |
| project_2_nl2sql (natural language to read-only SQL) | Not built yet |
| project_3_dashboard (spreadsheet dashboard) | Not built yet |

Not yet verified: running on a different PC, running with the network adapter fully disabled.

## Layout

    OfflineAISuite/
      ENV.bat                 activates the bundled Python for the current window
      CHECK_SUITE.bat         runs VERIFY_SUITE.py with the bundled Python
      VERIFY_SUITE.py         environment, package, model and integrity checks
      application/            shared runtime (python, site-packages, wheelhouse, llm_runtime, models, ocr, shared, scripts, tests)
      project_1_documents/
      project_2_nl2sql/
      project_3_dashboard/

## Design decisions

- Runtime: llama.cpp (llama-server.exe). A plain folder of exe and DLL files that reads .gguf files directly. Ollama is not used at runtime.
- Python: the Windows embeddable distribution plus a local site-packages folder. A virtual environment is NOT used, because venvs store absolute paths and break when moved.
- Packages: pinned in application/requirements.in, frozen in requirements.lock.txt, with every wheel in application/wheelhouse for offline reinstallation.
- OCR: Tesseract only (English plus orientation data).
- Retrieval: BM25 first. No vector database. BGE-M3 is present as an optional upgrade.
- Hardware profile: cpu_16gb (4 threads for generation, 8 for prompt processing, 4096 context). One model is loaded at a time.

## Daily use

Open Command Prompt in the suite folder:

    call ENV.bat
    CHECK_SUITE.bat --quick

Project launchers (START_PROJECT_1.bat and so on) are added with each project.

## Sharing

Copy the entire OfflineAISuite folder (about 22 GB with models) to the other PC. Do not copy only parts of it. See SETUP_STEPS.md.

## Documents

- SETUP_STEPS.md: preparation, transfer, first run, troubleshooting
- PORTABILITY_AND_SECURITY.md: supported conditions, verified and unverified claims, controls
- application/models/MODEL_INVENTORY.md: model list with sizes and SHA-256
- application/LICENSES/THIRD_PARTY.md: third-party components and licence notes

## Next Phase (not built)
| # | Item |
|---|---|
| N1 | Plan-based calculator for Project 1 (differences, ratios, percent change, group-by, date ranges, "North and South") |
| N2 | PDF table extraction into dataframes |
| N3 | Cross-file calculations |
| N4 | Calculated values handed to the model for wording, validated against the computed set |
| N5 | Benchmark of plan quality across models and larger data |
| N6 | Optional BGE-M3 retrieval, only if BM25 fails on real documents |
| N7 | GPU build and high-RAM profile testing |
| N8 | Concurrency tests, very large PDFs, .xls support |
| N9 | Lowercase unknown-value guard in the Project 1 calculator |
| N10 | Project 1 real-use fixes: multi-step notice, duplicate-save warning, citation wording |
| N11 | Project 2 optional table selection for very large schemas |
| N12 | Project 3: period-over-period change, top-N plus Other, correlation view, CSV export, cross-file joins |"# OfficeAISuite" 
