"""FastAPI application entry point for the PodVault web interface."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from podvault.web.auth import BasicAuthMiddleware
from podvault.web.routes import router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="PodVault")
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(router)


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz():
    """Unauthenticated liveness check for Docker's HEALTHCHECK (see web/auth.py)."""
    return "ok"
