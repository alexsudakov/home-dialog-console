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

APP_VERSION = "0.1.3"
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


def hdc_check() -> dict[str, Any]:
    return {
        "id": "home_dialog_console",
        "title": "Home Dialog Console",
        "ok": True,
        "status": "ok",
        "message": "",
        "details": {"version": APP_VERSION},
    }


async def get_json(url: str, timeout: float = 8.0) -> tuple[bool, int | None, int, Any, str]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            payload: Any = response.json()
        except Exception:
            payload = response.text[:1000]
        return 200 <= response.status_code < 300, response.status_code, elapsed_ms, payload, ""
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return False, None, elapsed_ms, None, f"{type(exc).__name__}: {exc or 'no details'}"


def fallback_summary(options: dict[str, Any], url: str, status_code: int | None, elapsed_ms: int, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "degraded",
        "generated_at": int(time.time()),
        "service": "home-dialog-console",
        "env": "local",
        "version": APP_VERSION,
        "summary": {"total": 2, "ok": 1, "failed": 1, "not_checked": 0},
        "options": public_options(options),
        "checks": [
            hdc_check(),
            {
                "id": "dialog_service",
                "title": "dialog-service",
                "ok": False,
                "status": "error",
                "message": error,
                "details": {"url": url, "status_code": status_code, "elapsed_ms": elapsed_ms},
            },
        ],
    }


async def build_diagnostics() -> dict[str, Any]:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    diagnostics_url = f"{dialog_service_url}/admin/diagnostics/summary"
    ok, status_code, elapsed_ms, payload, error = await get_json(diagnostics_url)

    if ok and isinstance(payload, dict):
        checks = [hdc_check()]
        checks.extend(payload.get("checks") or [])
        summary = dict(payload.get("summary") or {})
        summary["total"] = int(summary.get("total", 0)) + 1
        summary["ok"] = int(summary.get("ok", 0)) + 1
        return {
            **payload,
            "hdc_version": APP_VERSION,
            "dialog_service_url": dialog_service_url,
            "options": public_options(options),
            "summary": summary,
            "checks": checks,
        }

    return fallback_summary(options, diagnostics_url, status_code, elapsed_ms, error)


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
