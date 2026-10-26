"""Project 3 web API. Binds to 127.0.0.1 only. The browser sends ids, never paths.
No model is used in this module: plans come from suggestions or dropdowns and are validated before running."""
import re
from pathlib import Path
import threading
from shared import llm_manager

from . import ask as askmod
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from shared.safe_files import UploadError

from . import config, engine, suggest
from .plan import PlanError
from .profile import DataError
from .workspace import BatchError, Workspace

WEB_DIR = Path(__file__).resolve().parent / "web"
SESSION = re.compile(r"^[0-9a-f]{32}$")


class RunBody(BaseModel):
    batch: str = Field(max_length=64)
    file_id: str = Field(max_length=64)
    sheet: str = Field(max_length=200)
    plan: dict


class Target(BaseModel):
    file_id: str = Field(max_length=64)
    sheet: str = Field(max_length=200)

class AskBody(BaseModel):
    batch: str = Field(max_length=64)
    file_id: str = Field(max_length=64)
    sheet: str = Field(max_length=200)
    request: str = Field(max_length=400)
    model_id: str | None = Field(default=None, max_length=100)

class CompareBody(BaseModel):
    batch: str = Field(max_length=64)
    targets: list[Target] = Field(max_length=10)
    plan: dict


def create_app(workspace_dir=None, port=8110, llm=None):
    ws = Workspace(workspace_dir or config.WORKSPACE_DIR)
    sessions = set()
    server = llm or llm_manager.LlamaServer(profile="cpu_16gb", port=port + 1, log_dir=config.LOG_DIR)
    lock = threading.Lock()
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "testserver"}
    app = FastAPI(title="Project 3", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"error": "Invalid host."}, status_code=400)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "img-src 'self' data: blob:")
        return resp

    def need(token):
        if not SESSION.match(token or "") or token not in sessions:
            raise HTTPException(status_code=401, detail="Session expired. Reload the page.")

    def fail(msg, code=400):
        raise HTTPException(status_code=code, detail=str(msg))

    def static(name, media=None):
        p = WEB_DIR / name
        if not p.is_file():
            raise HTTPException(status_code=404, detail="Not installed.")
        return FileResponse(p, media_type=media)

    @app.get("/")
    def index():
        return static("index.html")

    @app.get("/app.js")
    def app_js():
        return static("app.js", "text/javascript")

    @app.get("/plotly.min.js")
    def plotly_js():
        return static("plotly.min.js", "text/javascript")

    @app.post("/api/session")
    def new_session():
        import uuid
        t = uuid.uuid4().hex
        sessions.add(t)
        return {"token": t}

    @app.get("/api/limits")
    def limits(x_session_token: str = Header(default="")):
        need(x_session_token)
        return {"allowed": sorted(config.ALLOWED_KINDS), "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
                "max_files": config.MAX_FILES, "max_compare": config.MAX_COMPARE, "max_rows": config.MAX_ROWS}

    @app.post("/api/batch")
    def new_batch(x_session_token: str = Header(default="")):
        need(x_session_token)
        return {"batch": ws.new_batch()}

    @app.post("/api/upload")
    async def upload(batch: str, file: UploadFile = File(...), x_session_token: str = Header(default="")):
        need(x_session_token)
        try:
            ds = ws.add_upload(batch, file.file, file.filename or "file")
        except (UploadError, DataError, BatchError) as exc:
            fail(exc, 409 if isinstance(exc, BatchError) else 400)
        return ds.public()

    @app.get("/api/files")
    def files(batch: str, x_session_token: str = Header(default="")):
        need(x_session_token)
        try:
            listing = ws.files(batch)
            for f in listing:
                for t in f["tables"]:
                    t["suggestions"] = suggest.suggest(ws.get_table(batch, f["id"], t["sheet"]))
            return {"files": listing}
        except BatchError as exc:
            fail(exc, 409)

    def table_for(batch, file_id, sheet):
        try:
            return ws.get_table(batch, file_id, sheet)
        except BatchError as exc:
            fail(exc, 409)

    @app.post("/api/run")
    def run(body: RunBody, x_session_token: str = Header(default="")):
        need(x_session_token)
        df = table_for(body.batch, body.file_id, body.sheet)
        try:
            return engine.execute(df, body.plan)
        except PlanError as exc:
            fail(exc, 422)

    def compare_tables(body_batch, targets):
        out, seen = [], set()
        listing = {f["id"]: f for f in ws.files(body_batch)}
        for t in targets:
            if (t.file_id, t.sheet) in seen:
                fail("Each file and sheet can be chosen only once.")
            seen.add((t.file_id, t.sheet))
            f = listing.get(t.file_id)
            if f is None:
                fail("Unknown file for this batch.", 409)
            label = f["name"] if len(f["tables"]) == 1 else f"{f['name']} / {t.sheet}"
            out.append({"name": label, "df": table_for(body_batch, t.file_id, t.sheet)})
        return out

    @app.post("/api/compare-suggestions")
    def compare_sugg(body: CompareBody, x_session_token: str = Header(default="")):
        need(x_session_token)
        try:
            tables = compare_tables(body.batch, body.targets)
        except BatchError as exc:
            fail(exc, 409)
        if not 2 <= len(tables) <= config.MAX_COMPARE:
            fail(f"Choose between 2 and {config.MAX_COMPARE} sheets to compare.")
        return {"shared": suggest.shared_columns(tables), "suggestions": suggest.comparison_suggestions(tables)}

    @app.post("/api/compare")
    def compare(body: CompareBody, x_session_token: str = Header(default="")):
        need(x_session_token)
        try:
            tables = compare_tables(body.batch, body.targets)
            return engine.compare(tables, body.plan)
        except BatchError as exc:
            fail(exc, 409)
        except PlanError as exc:
            fail(exc, 422)

    @app.post("/api/ask")
    def ask(body: AskBody, x_session_token: str = Header(default="")):
        need(x_session_token)
        mid = body.model_id or llm_manager.default_model_id("dashboard")
        if mid not in [m["id"] for m in llm_manager.verified_models("dashboard")]:
            fail("That model is not available for the dashboard.")
        df = table_for(body.batch, body.file_id, body.sheet)
        with lock:
            try:
                plan = askmod.plan_from_request(body.request, df, server, mid)
            except askmod.AskError as exc:
                fail(exc, 422)
            except llm_manager.LLMError as exc:
                fail(f"The model could not answer: {exc}", 503)
        if plan is None:
            fail("These columns cannot answer that request. Try one of the ready-made views.", 422)
        try:
            res = engine.execute(df, plan)
        except PlanError as exc:
            fail(exc, 422)
        res["plan"] = plan
        return res

    @app.get("/api/models")
    def models(x_session_token: str = Header(default="")):
        need(x_session_token)
        return {"models": [{"id": m["id"], "name": m["name"]} for m in llm_manager.verified_models("dashboard")],
                "default": llm_manager.default_model_id("dashboard")}

    @app.post("/api/clear")
    def clear(x_session_token: str = Header(default="")):
        need(x_session_token)
        ws.clear()
        return {"cleared": True}

    return app