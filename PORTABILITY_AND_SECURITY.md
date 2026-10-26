# Portability and Security

## Supported conditions

- Windows 10 or 11, 64-bit, with a normal user account.
- 16 GB RAM is the sizing target. CPU-only.
- Disk: about 22 GB for the suite as it stands.

## What has been verified (preparation PC: i5-1135G7, 16 GB, Windows)

- Bundled Python 3.11.9 runs and imports all 16 pinned packages from application\site-packages.
- The wheelhouse installs every pinned package with pip --no-index (dry-run).
- llama-server.exe and tesseract.exe start from inside the suite folder.
- All 8 models have valid GGUF headers. Models were loaded and answered through llm_check.py and the manager. Measured generation speeds are in MODEL_INVENTORY.md.
- Starting a model, switching to another and stopping leaves no llama-server process running.
- Shared code tests (application\tests) pass: upload validation by content, path traversal blocking, cleanup refusing unmarked folders.
- CHECK_SUITE.bat reports no machine-specific user paths in suite files.
- Each project’s automated tests pass; each app starts on 127.0.0.1 only; browser-checked on the preparation PC.

## What has NOT been verified

- Running on a second PC, and on a PC with no Visual C++ runtime installed.
- Running with the network adapter completely disabled (a manual test, listed in SETUP_STEPS.md).
- Whether tesseract.exe from the installer works on a PC where the installer was never run.
- GPU use. No GPU build is bundled.

## Offline behaviour

- The app code uses only 127.0.0.1 for model servers. Nothing downloads at launch.
- Internet is needed only during preparation (Python, wheels, llama.cpp, Tesseract, models).
- pip is never run against the internet on the destination PC. Reinstallation uses application\wheelhouse with --no-index.

## Trust boundaries

- Uploaded files, document text, spreadsheet contents and model output are untrusted.
- The browser is untrusted. All validation happens on the server.
- Model files are trusted only because you placed them; the manifest detects later changes.

## Controls in place (shared code)

- Uploads are checked by file content (magic bytes, xlsx structure), not by extension. Size limit enforced while streaming. Files are stored under random names. Partial files are removed on failure.
- Filenames are sanitised. Directory parts and reserved Windows names are neutralised. Resolved paths must stay inside the target folder.
- Cleanup deletes only folders carrying a .suite_owned marker and refuses any other folder. Symlinks are not followed.
- The model server binds to 127.0.0.1 and refuses to start if its port is already taken.
- Logs are bounded by rotate_logs. Prompts and documents are not logged.

## Controls planned (not built yet)

- Project 1: selected-files-only answering, file registry with SHA-256 change detection, explicit save and delete actions, session tokens on state-changing endpoints.
- Project 2: sqlglot allowlist validation, schema validation, read-only SQLite connection with authorizer, timeouts and row limits, stale-schema detection.
- Project 3: temporary workspace only, replaced on each new upload batch, validated analysis plans, no model-generated code.

## Known limitations

- Antivirus software may flag or slow large model files.
- Hard-linked model files exist only on the preparation drive. Copies are full files.
- Licence notes are from memory and need checking before redistribution.