from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_VERSION = "0.1.9"
CONFIG_PATH = Path("/data/options.json")
DEFAULT_DIALOG_SERVICE_URL = "http://127.0.0.1:8090"
BASE_DIR = Path(__file__).resolve().parent

SERVICE_CARD_MAP: dict[str, dict[str, str]] = {
    "dialog_service": {"title": "dialog-service", "icon": "💬", "description": "Основной диалоговый сервис и обработка запросов."},
    "planner_llama": {"title": "Planner llama.cpp", "icon": "📋", "description": "Планирование маршрута ответа и выбор действий."},
    "redis": {"title": "Redis", "icon": "🧱", "description": "Кэш, очереди и служебное состояние."},
    "telegram_runner": {"title": "Telegram runner", "icon": "✈️", "description": "Приём и отправка сообщений Telegram."},
    "ha_api": {"title": "HA API", "icon": "🏠", "description": "Доступ к Home Assistant API."},
    "source_selector": {"title": "Source Selector", "icon": "🧭", "description": "Выбор нужных источников данных."},
    "qdrant": {"title": "Qdrant", "icon": "🧩", "description": "Векторная база для карточек источников."},
    "llm_log_helper": {"title": "llm_log_helper", "icon": "📄", "description": "Чтение логов Home Assistant и add-ons."},
    "ollama": {"title": "Ollama fallback", "icon": "🧠", "description": "Резервная локальная LLM-служба."},
    "config_db": {"title": "Config DB", "icon": "🗄️", "description": "SQLite-настройки, алиасы и журнал действий."},
    "action_executor": {"title": "Action Executor", "icon": "⚙️", "description": "Безопасная проверка и выполнение действий."},
    "system_runtime_snapshot": {"title": "System Snapshot", "icon": "🕒", "description": "Свежесть снимка runtime-состояния."},
}

DEPENDENCY_IDS = ["redis", "ha_api", "telegram_runner", "planner_llama", "source_selector", "qdrant", "llm_log_helper", "ollama"]

NAV_ITEMS = [
    {"title": "Обзор", "href": "/", "active": True},
    {"title": "Окружение", "href": "#environment", "active": False},
    {"title": "Qdrant / Source Selector", "href": "#qdrant", "active": False},
    {"title": "Промты", "href": "#prompts", "active": False},
    {"title": "Анализаторы", "href": "#analyzers", "active": False},
    {"title": "Действия FastPath", "href": "#actions", "active": False},
    {"title": "Объекты и алиасы", "href": "#objects", "active": False},
    {"title": "База данных", "href": "#database", "active": False},
    {"title": "Логи", "href": "#logs", "active": False},
    {"title": "О системе", "href": "#about", "active": False},
]

app = FastAPI(title="Home Dialog Console", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def load_options() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"dialog_service_url": os.getenv("DIALOG_SERVICE_URL", DEFAULT_DIALOG_SERVICE_URL), "log_level": os.getenv("LOG_LEVEL", "info")}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {"dialog_service_url": data.get("dialog_service_url") or DEFAULT_DIALOG_SERVICE_URL, "log_level": data.get("log_level") or "info"}


def public_options(options: dict[str, Any]) -> dict[str, Any]:
    return {"dialog_service_url": options.get("dialog_service_url"), "log_level": options.get("log_level")}


def hdc_check() -> dict[str, Any]:
    return {"id": "home_dialog_console", "title": "Home Dialog Console", "ok": True, "status": "ok", "message": "", "details": {"version": APP_VERSION}}


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
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service": "home-dialog-console",
        "env": "local",
        "version": APP_VERSION,
        "summary": {"total": 2, "ok": 1, "failed": 1, "not_checked": 0, "incidents": 1, "recommendations": 1, "action_blocks": 0},
        "options": public_options(options),
        "incidents": [{"id": "dialog_service_unavailable", "title": "dialog-service", "severity": "error", "message": error}],
        "recommendations": [{"id": "dialog_service_unavailable", "title": "dialog-service", "text": "Проверить доступность dialog-service URL из настроек HDC."}],
        "action_blocks": [],
        "checks": [hdc_check(), {"id": "dialog_service", "title": "dialog-service", "ok": False, "status": "error", "message": error, "details": {"url": url, "status_code": status_code, "elapsed_ms": elapsed_ms}}],
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
            "incidents": payload.get("incidents") or [],
            "recommendations": payload.get("recommendations") or [],
            "action_blocks": payload.get("action_blocks") or [],
            "checks": checks,
        }

    return fallback_summary(options, diagnostics_url, status_code, elapsed_ms, error)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def format_dt(value: Any, fallback: str = "нет данных") -> str:
    dt = parse_dt(value)
    if not dt:
        return fallback
    return dt.astimezone().strftime("%d.%m.%Y %H:%M:%S")


def format_time(value: Any, fallback: str = "нет данных") -> str:
    dt = parse_dt(value)
    if not dt:
        return fallback
    return dt.astimezone().strftime("%H:%M:%S")


def status_label(check: dict[str, Any]) -> str:
    if check.get("ok") is True:
        return "Работает"
    if check.get("ok") is False:
        return "Ошибка"
    status = str(check.get("status") or "")
    return {"disabled": "Отключено", "not_configured": "Не настроено", "not_found": "Не найдено", "stale": "Устарело"}.get(status, "Не проверялось")


def state_class(check: dict[str, Any]) -> str:
    if check.get("ok") is True:
        return "ok"
    if check.get("ok") is False:
        return "bad"
    if str(check.get("status") or "") == "stale":
        return "warning"
    return "neutral"


def check_by_id(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(check.get("id")): check for check in checks}


def visible_recommendations(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in recommendations if str(item.get("id") or "") != "all_ok"]


def build_service_cards(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = check_by_id(checks)
    cards: list[dict[str, Any]] = []
    for check_id, meta in SERVICE_CARD_MAP.items():
        check = by_id.get(check_id)
        if not check:
            continue
        details = check.get("details") or {}
        elapsed_ms = details.get("elapsed_ms")
        cards.append({
            "id": check_id,
            "title": meta["title"],
            "icon": meta["icon"],
            "description": meta["description"],
            "label": status_label(check),
            "status": check.get("status") or "unknown",
            "class": state_class(check),
            "elapsed_ms": f"{elapsed_ms} мс" if elapsed_ms is not None else "—",
            "message": check.get("message") or "",
            "details": details,
        })
    return cards


def build_summary_tiles(diagnostics: dict[str, Any], recommendations_visible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = diagnostics.get("summary") or {}
    total = int(summary.get("total") or 0)
    ok = int(summary.get("ok") or 0)
    availability = round((ok / total) * 100, 1) if total else 0
    return [
        {"label": "Всего проверок", "value": total, "hint": "включая HDC"},
        {"label": "OK", "value": ok, "hint": "работают штатно"},
        {"label": "Ошибки", "value": int(summary.get("failed") or 0), "hint": "критичные сбои"},
        {"label": "Инциденты", "value": int(summary.get("incidents") or 0), "hint": "runtime-ошибки"},
        {"label": "Рекомендации", "value": len(recommendations_visible), "hint": "что проверить"},
        {"label": "Блокировки", "value": int(summary.get("action_blocks") or 0), "hint": "защита действий"},
        {"label": "Доступность", "value": f"{availability}%", "hint": "OK / всего"},
    ]


def build_snapshot(checks: list[dict[str, Any]]) -> dict[str, Any]:
    check = check_by_id(checks).get("system_runtime_snapshot") or {}
    details = check.get("details") or {}
    try:
        age_int = int(details.get("age_sec"))
    except Exception:
        age_int = None

    if check.get("ok") is True and age_int is not None and age_int <= 180:
        label, css = "Актуален", "ok"
    elif check.get("ok") is False:
        label, css = "Ошибка", "bad"
    else:
        label, css = "Устарел или не найден", "warning"

    return {"label": label, "class": css, "age": f"{age_int} сек" if age_int is not None else "нет данных", "updated_at": format_dt(details.get("updated_at")), "path": details.get("path") or "—"}


def build_dependency_rows(checks: list[dict[str, Any]], recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = check_by_id(checks)
    rec_by_id = {str(item.get("id")): item for item in recommendations}
    rows: list[dict[str, Any]] = []
    for check_id in DEPENDENCY_IDS:
        check = by_id.get(check_id)
        if not check:
            continue
        details = check.get("details") or {}
        rows.append({
            "title": SERVICE_CARD_MAP.get(check_id, {}).get("title", check.get("title", check_id)),
            "label": status_label(check),
            "class": state_class(check),
            "elapsed_ms": details.get("elapsed_ms", "—"),
            "error": check.get("message") or "—",
            "recommendation": (rec_by_id.get(check_id) or {}).get("text") or "Нет рекомендаций",
        })
    return rows


def build_view_model(diagnostics: dict[str, Any]) -> dict[str, Any]:
    checks = diagnostics.get("checks") or []
    incidents = diagnostics.get("incidents") or []
    recommendations_all = diagnostics.get("recommendations") or []
    recommendations_visible = visible_recommendations(recommendations_all)
    status = diagnostics.get("status") or "unknown"
    summary = diagnostics.get("summary") or {}

    has_problem = bool(summary.get("failed") or summary.get("incidents") or incidents)
    if status == "ok" and not has_problem:
        overall = {"class": "ok", "title": "Система работает нормально", "description": "Все ключевые сервисы доступны и функционируют в штатном режиме.", "label": "здорово"}
    elif status == "degraded":
        overall = {"class": "warning", "title": "Система работает с предупреждениями", "description": "Есть некритичные проблемы или устаревшие данные, требующие внимания.", "label": "предупреждение"}
    else:
        overall = {"class": "bad", "title": "Система работает с ошибками", "description": "Одна или несколько ключевых зависимостей недоступны.", "label": "ошибка"}

    return {
        "nav_items": NAV_ITEMS,
        "overall": overall,
        "updated_at": format_time(diagnostics.get("generated_at")),
        "updated_at_full": format_dt(diagnostics.get("generated_at")),
        "service_cards": build_service_cards(checks),
        "summary_tiles": build_summary_tiles(diagnostics, recommendations_visible),
        "snapshot": build_snapshot(checks),
        "dependency_rows": build_dependency_rows(checks, recommendations_visible),
        "incidents": incidents,
        "recommendations": recommendations_visible,
        "recommendations_empty_text": "Действий не требуется: все диагностические проверки зелёные.",
        "action_blocks": diagnostics.get("action_blocks") or [],
        "checks": checks,
        "raw": diagnostics,
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
    view = build_view_model(diagnostics)
    return templates.TemplateResponse(request, "index.html", {"diagnostics": diagnostics, "view": view, "options": diagnostics["options"]})
