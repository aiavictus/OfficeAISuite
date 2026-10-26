"""Project 2 web API. Binds to 127.0.0.1 only. The browser sends ids, never paths. Never runs write SQL."""
import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from shared import llm_manager
from shared.cleanup import mark_owned

from . import config, executor, generate
from .catalog import DatabaseCatalog, SchemaError, sniff_sqlite, validate_db
from .validate import SqlRejected, check_sql

WEB_DIR = Path(__file__).resolve().parent / "web"
SESSION = re.compile(r"^[0-9a-f]{32}$")


class AskBody(BaseModel):
    db_id: str = Field(max_length=300)
    question: str = Field(max_length=500)
    model_id: str | None = Field(default=None, max_length=100)


class SqlBody(BaseModel):
    db_id: str = Field(max_length=300)
    sql: str = Field(max_length=4000)


def create_app(db_dir=None, data_dir=None, schema_dir=None, llm=None, port=8100):
    db_dir = Path(db_dir or config.DB_DIR)
    data_dir = Path(data_dir or config.DATA_DIR)
    catalog = DatabaseCatalog(db_dir, data_dir / "schema_cache", schema_dir or config.SCHEMA_DIR)
    server = llm or llm_manager.LlamaServer(profile="cpu_16gb", port=port + 1, log_dir=config.LOG_DIR)
    lock = threading.Lock()
    sessions = set()
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "testserver"}
    app = FastAPI(title="Project 2", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"error": "Invalid host."}, status_code=400)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
        return resp

    def need(token):
        if not SESSION.match(token or "") or token not in sessions:
            raise HTTPException(status_code=401, detail="Session expired. Reload the page.")

    def fail(msg, code=400):
        raise HTTPException(status_code=code, detail=str(msg))

    def model_for(body_model):
        mid = body_model or llm_manager.default_model_id("sql")
        cfg = next((m for m in llm_manager.verified_models("sql") if m["id"] == mid), None)
        if cfg is None:
            fail("That model is not available for SQL generation.")
        return mid, cfg

    @app.get("/")
    def index():
        p = WEB_DIR / "index.html"
        if not p.is_file():
            raise HTTPException(status_code=404, detail="Interface not installed yet.")
        return FileResponse(p)

    @app.get("/app.js")
    def app_js():
        p = WEB_DIR / "app.js"
        if not p.is_file():
            raise HTTPException(status_code=404, detail="Interface script not installed.")
        return FileResponse(p, media_type="text/javascript")

    @app.post("/api/session")
    def new_session():
        t = uuid.uuid4().hex
        sessions.add(t)
        return {"token": t}

    @app.get("/api/state")
    def state(x_session_token: str = Header(default="")):
        need(x_session_token)
        scan = catalog.scan()
        catalog.prune()
        return {"databases": [dict(r.public(), description=catalog.describe(r.name)) for r in scan.records],
                "changes": {"new": [r.name for r in scan.new], "modified": [r.name for r in scan.modified],
                            "removed": scan.removed, "invalid": [n for n, _ in scan.invalid], "busy": scan.busy},
                "models": [{"id": m["id"], "name": m["name"], "ram_gb_est": m.get("ram_gb_est")}
                           for m in llm_manager.verified_models("sql")],
                "default_model": llm_manager.default_model_id("sql"),
                "limits": {"max_db_mb": config.MAX_DB_BYTES // (1024 * 1024), "allowed": list(config.DB_EXTENSIONS)}}

    @app.get("/api/schema")
    def schema(db_id: str, x_session_token: str = Header(default="")):
        need(x_session_token)
        try:
            return catalog.get_schema(db_id)
        except SchemaError as exc:
            fail(exc, 404)

    def run(body, execute, token):
        need(token)
        mid, cfg = model_for(body.model_id)
        try:
            sch = catalog.get_schema(body.db_id)          # fresh scan every request: never a stale schema
        except SchemaError as exc:
            fail(exc, 404)
        with lock:
            try:
                gen = generate.generate_sql(body.question, sch, server, mid, cfg)
            except llm_manager.LLMError as exc:
                fail(f"The model could not answer: {exc}", 503)
            out = {"stage": "generation", "status": gen.status, "sql": gen.sql, "reason": gen.reason,
                   "database": sch["name"], "executed": False}
            if gen.status != "ok" or not execute:
                return out
            try:
                rec = catalog.resolve(body.db_id)
                res = executor.run_query(rec, gen.checked)
            except (SchemaError, executor.QueryError) as exc:
                out.update(stage="execution", status="execution_failed", reason=str(exc))
                return out
            out.update(stage="execution", status="ok", executed=True, columns=res.columns, rows=res.rows,
                       truncated=res.truncated, elapsed_ms=res.elapsed_ms)
            return out

    @app.post("/api/generate")
    def gen_only(body: AskBody, x_session_token: str = Header(default="")):
        return run(body, False, x_session_token)

    @app.post("/api/preview")
    def gen_preview(body: AskBody, x_session_token: str = Header(default="")):
        return run(body, True, x_session_token)

    @app.post("/api/run-sql")
    def run_sql(body: SqlBody, x_session_token: str = Header(default="")):
        """Run user-edited SQL: re-validated against the live schema, read-only executor. No model involved."""
        need(x_session_token)
        try:
            sch = catalog.get_schema(body.db_id)
        except SchemaError as exc:
            fail(exc, 404)
        try:
            checked = check_sql(body.sql, sch)
        except SqlRejected as exc:
            return {"stage": "validation", "status": "rejected", "reason": str(exc), "executed": False}
        try:
            res = executor.run_query(catalog.resolve(body.db_id), checked)
        except (SchemaError, executor.QueryError) as exc:
            return {"stage": "execution", "status": "execution_failed", "reason": str(exc),
                    "sql": checked.sql, "executed": False}
        return {"stage": "execution", "status": "ok", "sql": checked.sql, "executed": True,
                "columns": res.columns, "rows": res.rows, "truncated": res.truncated,
                "elapsed_ms": res.elapsed_ms}

    @app.post("/api/upload-db")
    async def upload_db(file: UploadFile = File(...), x_session_token: str = Header(default="")):
        need(x_session_token)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", (file.filename or "").replace("\\", "/").split("/")[-1]).strip("._")
        if not name.lower().endswith(config.DB_EXTENSIONS):
            fail("Unsupported file type. Allowed: " + ", ".join(config.DB_EXTENSIONS))
        stage = mark_owned(data_dir / "upload_tmp")
        part = stage / (uuid.uuid4().hex + ".part")
        total = 0
        try:
            with open(part, "wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > config.MAX_DB_BYTES:
                        fail("File too large (limit %d MB)." % (config.MAX_DB_BYTES // (1024 * 1024)))
                    out.write(chunk)
            if total == 0 or not sniff_sqlite(part) or not validate_db(part):
                fail("The file is not a readable SQLite database.")
            stem, ext = Path(name).stem, Path(name).suffix
            for n in range(1, 1000):
                final = db_dir / (name if n == 1 else f"{stem} ({n}){ext}")
                if not final.exists():
                    break
            else:
                fail("Could not find a free file name.")
            part.replace(final)
        finally:
            part.unlink(missing_ok=True)
        return {"saved_as": final.name}

    return app