# Setup Steps

## A. Preparation PC (internet needed only here)

Everything is placed inside the suite folder. Use Command Prompt, never PowerShell scripts.

1. Create the folder tree (see README layout).
2. Download python-3.11.9-embed-amd64.zip and get-pip.py into application\_downloads. Extract the zip into application\python.
3. Replace the content of application\python\python311._pth with these four lines:

       python311.zip
       .
       ..\site-packages
       import site

4. From the suite folder:

       call ENV.bat
       python application\_downloads\get-pip.py --target application\site-packages --no-warn-script-location
       python -m pip download -r application\requirements.in -d application\wheelhouse --only-binary=:all:
       python -m pip download pip -d application\wheelhouse
       python -m pip install --no-index --find-links application\wheelhouse --target application\site-packages --upgrade -r application\requirements.in --no-warn-script-location
       python -m pip freeze --path application\site-packages > application\requirements.lock.txt

5. Download a numbered llama.cpp release zip for Windows CPU x64 and extract it so llama-server.exe sits in application\llm_runtime\llama.cpp. Record the tag, zip name and SHA-256 in RUNTIME_VERSION.txt. Copy vcruntime140.dll, vcruntime140_1.dll and msvcp140.dll beside llama-server.exe and beside python.exe if they are missing.
6. Tesseract: install the 64-bit Windows build into application\ocr\tesseract (English and OSD only), then check "tesseract --list-langs".
7. Place the .gguf files in application\models and describe each one in application\models\models.json. Models that came from Ollama are hard-linked with application\scripts\link_ollama_models.bat (same drive only).
8. Test each model:

       python application\scripts\llm_check.py --list
       python application\scripts\llm_check.py <model_id>

9. Create the manifest and verify:

       python application\scripts\make_manifest.py
       CHECK_SUITE.bat

10. Delete application\_downloads if you do not want to ship installers.
11. Disconnect from the internet and run CHECK_SUITE.bat --load once.

## B. Transfer

- Copy the whole folder with Explorer or robocopy to an external drive. Hard-linked model files become ordinary full files when copied, so the copy needs the full model size.
- Keep the folder name and location free to change. Nothing inside depends on the drive letter.
- Zipping is optional. If you zip, extract fully before running.

## C. Destination PC (no internet, no administrator)

1. Copy the folder anywhere you can write, for example D:\OfflineAISuite.
2. Open Command Prompt in that folder and run:

       CHECK_SUITE.bat --full

   --full re-hashes every file including models (a few minutes).
3. Every line must be PASS. Fix any FAIL before continuing.
4. Run CHECK_SUITE.bat --load to start the default models.

## D. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| ModuleNotFoundError | The system Python was used. Run "call ENV.bat" first. |
| FAIL: running the bundled python | Same cause. Always start through CHECK_SUITE.bat or after ENV.bat. |
| Missing VCRUNTIME140 or MSVCP140 DLL | Copy the three DLLs into application\llm_runtime\llama.cpp and application\python. |
| Port 8090 or 8100 or 8110 in use | Another llama-server is running. End it in Task Manager, or change the port in llm_profiles.json. |
| Model not ready after 300 s | Close other programs to free RAM, check application\logs\llama_<model>.log. |
| manifest integrity FAIL | A file changed or was damaged in transfer. Recopy it. If the change was intended, run make_manifest.py. |
| Out of memory while loading | Use a 4B model, or close other applications. 16 GB RAM is the sizing target. |
| Antivirus quarantines a file | Restore it and allow the folder. Then run CHECK_SUITE.bat --full. |

## E. Faster hardware (no code change)

- 32 GB RAM or more: use the high_ram_32gb profile (16384 context). Larger quantizations and bigger models become possible.
- GPU: needs a CUDA or Vulkan llama.cpp build placed in application\llm_runtime. It is not bundled, so GPU use is unverified. Set gpu_layers in a profile after testing.