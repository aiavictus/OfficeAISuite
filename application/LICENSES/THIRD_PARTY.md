# Third-party components

Licence column is from memory. Verify each one against its official page before sharing the suite.

| Component | Used for | Licence (verify) | Source to record |
|---|---|---|---|
| Python 3.11.9 embeddable | runtime | PSF License | python.org |
| pip and PyPI packages in requirements.lock.txt | libraries | mostly BSD, MIT, Apache-2.0 | each package's PyPI page |
| llama.cpp b11609 | model server | MIT | github.com/ggml-org/llama.cpp |
| Tesseract 5.5.x (UB-Mannheim build) | OCR | Apache-2.0 plus bundled libraries | github.com/UB-Mannheim/tesseract |
| Microsoft VC++ runtime DLLs | llama.cpp and Python | Microsoft redistribution terms (app-local use) | Windows System32 |
| Plotly JS bundle | charts (offline) | MIT | plotly package |
| Qwen3 4B, Qwen3 8B | documents, dashboard | Apache-2.0 | model card |
| Qwen2.5-Coder 7B Instruct | SQL | Apache-2.0 | model card |
| SQLCoder 7B | SQL | check model card | model card |
| DeepSeek-R1-Distill-Qwen 7B | optional | MIT | model card |
| Gemma 3 4B IT | optional | Gemma Terms of Use (restrictions apply) | model card |
| Llama 3.2 3B Instruct | optional | Llama 3.2 Community License (attribution required) | model card |
| BGE-M3 | optional embeddings | MIT | model card |

Model files were obtained by the suite owner. Write the exact download URL for each in the "Source to record" column.

Gemma and Llama licences carry conditions on redistribution. If you cannot accept them, delete those two files and their entries in models.json, then run make_manifest.py.