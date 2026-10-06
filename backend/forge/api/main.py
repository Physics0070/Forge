from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from forge.api import auth, projects
from forge.config import get_settings
from forge.db import get_engine
from forge.logging import log, setup_logging


def create_app() -> FastAPI:
    settings = get_settings()  # fails fast with a readable message on bad config
    setup_logging("api")
    app = FastAPI(title="FORGE API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-csrf-token", "x-workspace-id", "idempotency-key"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "same-origin"
        if settings.secure_cookies:
            response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request, exc: RequestValidationError):
        fields = [{"loc": [str(x) for x in e["loc"]], "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed.", "fields": fields}},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log("unhandled_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
        logging.getLogger("forge").exception("unhandled")
        return JSONResponse(
            {"error": {"code": "INTERNAL", "message": "Something went wrong. The incident was logged."}},
            status_code=500,
        )

    @app.get("/api/health")
    def health():
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return JSONResponse({"status": "ok" if db_ok else "degraded", "database": db_ok}, status_code=200 if db_ok else 503)

    app.include_router(auth.router)
    app.include_router(projects.router)
    return app


app = create_app()
