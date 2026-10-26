"""Project 1 web API. Binds to 127.0.0.1 only. The browser sends ids, never paths."""
import json
import threading
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from shared import llm_manager
from shared.paths import STATIC_DIR
from shared.safe_files import UploadError

from . import answer, config, extract
from .registry import FileRegistry, SelectionError
from .sessions import SessionError, SessionStore, resolve_selection

WEB_DIR = Path(__file__).resolve().parent / "web"


class AskBody(BaseModel):
    question: str = Field(max_length=1000)
    file_ids: list[str] = Field(max_length=20)
    model_id: str | None = Field(default=None, max_length=100)


class SaveBody(BaseModel):
    file_id: str = Field(max_length=100)


class DeleteBody(BaseModel):
    file_id: str = Field(max_length=300)
    confirm: bool = False


def create_app(repo_dir=None, data_dir=None, llm=None, port=8090):
    repo_dir = Path(repo_dir or config.REPO_DIR)
    data_dir = Path(data_dir or config.DATA_DIR)
    registry = FileRegistry(repo_dir, data_dir / "registry.sqlite", config.ALLOWED_KINDS)
    store = SessionStore(data_dir / "session_tmp", config.ALLOWED_KINDS, config.MAX_UPLOAD_BYTES)
    store.purge_all()                                   # leftovers from a previous run
    from shared.cleanup import mark_owned
    extracted = mark_owned(data_dir / "extracted")
    server = llm or llm_manager.LlamaServer(profile="cpu_16gb", port=port + 1,
                                            log_dir=config.LOG_DIR)
    ask_lock = threading.Lock()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "testserver"}

    app = FastAPI(title="Project 1", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"error": "Invalid host."}, status_code=400)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
        return resp

    def need_session(token):
        try:
            store.list_files(token)
        except SessionError:
            raise HTTPException(status_code=401, detail="Session expired. Reload the page.")
        return token

    def fail(exc, code=400):
        raise HTTPException(status_code=code, detail=str(exc))

    @app.get("/")
    def index():
        page = WEB_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=404, detail="Interface not installed yet.")
        return FileResponse(page)
    
    @app.get("/app.js")
    def app_js():
        js = WEB_DIR / "app.js"
        if not js.is_file():
            raise HTTPException(status_code=404, detail="Interface script not installed.")
        return FileResponse(js, media_type="text/javascript")

    @app.post("/api/session")
    def new_session():
        return {"token": store.new_session()}

    @app.get("/api/state")
    def state(x_session_token: str = Header(default="")):
        need_session(x_session_token)
        scan = registry.scan()
        extracted_names = {r.sha256 for r in scan.records}
        from .registry import prune_derived
        prune_derived(extracted, extracted_names | {r.sha256 for r in store.list_files(x_session_token)})
        return {
            "repository": [r.public() for r in scan.records],
            "session": [r.public() for r in store.list_files(x_session_token)],
            "changes": {"new": [r.name for r in scan.new], "modified": [r.name for r in scan.modified],
                        "removed": scan.removed, "invalid": scan.invalid, "busy": scan.busy},
            "models": [{"id": m["id"], "name": m["name"], "ram_gb_est": m.get("ram_gb_est")}
                       for m in llm_manager.verified_models("documents")],
            "default_model": llm_manager.default_model_id("documents"),
            "limits": {"max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
                       "allowed": sorted(config.ALLOWED_KINDS)},
        }


    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...), x_session_token: str = Header(default="")):
        need_session(x_session_token)
        try:
            rec = store.add_upload(x_session_token, file.file, file.filename or "file")
        except UploadError as exc:
            fail(exc)
        return {"file": rec.public(), "auto_selected": True}

    @app.post("/api/save")
    def save(body: SaveBody, x_session_token: str = Header(default="")):
        need_session(x_session_token)
        try:
            name = store.save_to_repository(x_session_token, body.file_id, registry)
        except (SessionError, SelectionError) as exc:
            fail(exc)
        return {"saved_as": name}

    @app.post("/api/delete")
    def delete(body: DeleteBody, x_session_token: str = Header(default="")):
        need_session(x_session_token)
        if not body.confirm:
            fail("Deleting a repository file needs confirm=true.")
        try:
            return {"deleted": registry.delete(body.file_id)}
        except SelectionError as exc:
            fail(exc, 404)

    @app.post("/api/clear")
    def clear(x_session_token: str = Header(default="")):
        need_session(x_session_token)
        store.clear(x_session_token)
        return {"token": store.new_session()}

    @app.post("/api/ask")
    def ask(body: AskBody, x_session_token: str = Header(default="")):
        need_session(x_session_token)
        model_id = body.model_id or llm_manager.default_model_id("documents")
        if model_id not in [m["id"] for m in llm_manager.verified_models("documents")]:
            fail("That model is not available for document questions.")
        try:
            records = resolve_selection(registry, store, x_session_token, body.file_ids)
            data = [extract.extract_file(r, extracted) for r in records]
        except (SelectionError, extract.ExtractError) as exc:
            fail(exc)
        with ask_lock:
            try:
                a = answer.answer_question(body.question, data, server, model_id)
            except llm_manager.LLMError as exc:
                fail(f"The model could not answer: {exc}", 503)
        return {"status": a.status, "answer": a.text, "citations": a.citations, "detail": a.detail,
                "used_files": [r.name for r in records]}

    return app