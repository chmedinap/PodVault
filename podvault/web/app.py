"""FastAPI application entry point for the PodVault web interface."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from podvault.web.routes import router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="PodVault")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(router)
