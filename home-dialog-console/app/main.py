from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_VERSION = "0.1.2"
CONFIG_PATH = Path("/data/options.json")
DEFAULT_DIALOG_SERVICE_URL = "http://127.0.0.1:8090"

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Home Dialog Console", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def load_options() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {
            "dialog_service_url": os.getenv("DIALOG_SERVICE_URL", DEFAULT_DIALOG_SERVICE_URL),
            "log_level": os.getenv("LOG_LEVEL", "info"),
        }

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    return {
        "dialog_service_url": data.get("dialog_service_url") or DEFAULT_DIALOG_SERVICE_URL,
        "log_level": data.get("log_level") or "info",
    }


def public_options(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "dialog_service_url": options.get("dialog_service_url"),
        "log_level": options.get("log_level"),
    }


async def check_http_json(name: str, url: str, timeout: float = 5.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        payload: Any
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:500]

        return {
            "name": name,
            "ok": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "url": url,
            "data": payload,
            "error": None,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return {
            "name": name,
            "ok": False,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
            "url": url,
            "data": None,
            "error": f"{type(exc).__name__}: {exc or 'no details'}",
        }


async def build_diagnostics() -> dict[str, Any]:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    dialog_health = await check_http_json(
        "dialog-service",
        f"{dialog_service_url}/health",
    )

    return {
        "version": APP_VERSION,
        "generated_at": int(time.time()),
        "options": public_options(options),
        "checks": [
            {
                "name": "home-dialog-console",
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 0,
                "url": "/health",
                "data": {"status": "ok", "version": APP_VERSION},
                "error": None,
            },
            dialog_health,
        ],
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "home-dialog-console", "version": APP_VERSION}


@app.get("/api/diagnostics/summary")
async def diagnostics_summary() -> JSONResponse:
    return JSONResponse(await build_diagnostics())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    diagnostics = await build_diagnostics()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "diagnostics": diagnostics,
            "checks": diagnostics["checks"],
            "options": diagnostics["options"],
        },
    )
