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

APP_VERSION = "0.1.20"
CONFIG_PATH = Path("/data/options.json")
DEFAULT_DIALOG_SERVICE_URL = "http://127.0.0.1:8090"
BASE_DIR = Path(__file__).resolve().parent

SERVICE_CARD_MAP: dict[str, dict[str, str]] = {
    "dialog_service": {"title": "dialog-service", "icon": "💬", "description": "Основной диалоговый сервис."},
    "planner_llama": {"title": "Planner", "icon": "📋", "description": "Планирование ответа и выбор действия."},
    "redis": {"title": "Redis", "icon": "🧱", "description": "Кэш, очереди и служебное состояние."},
    "telegram_runner": {"title": "Telegram", "icon": "✈️", "description": "Приём и отправка сообщений."},
    "ha_api": {"title": "HA API", "icon": "🏠", "description": "Доступ к Home Assistant."},
    "source_selector": {"title": "Source Selector", "icon": "🧭", "description": "Выбор нужных источников данных."},
    "qdrant": {"title": "Qdrant", "icon": "🧩", "description": "Векторная база карточек."},
    "llm_log_helper": {"title": "Log helper", "icon": "📄", "description": "Чтение логов HA и add-ons."},
    "ollama": {"title": "Ollama fallback", "icon": "🧠", "description": "Резервная локальная LLM-служба."},
    "config_db": {"title": "Config DB", "icon": "🗄️", "description": "SQLite-настройки и журналы."},
    "action_executor": {"title": "Action Executor", "icon": "⚙️", "description": "Безопасное выполнение действий."},
    "system_runtime_snapshot": {"title": "System Snapshot", "icon": "🕒", "description": "Свежесть runtime-снимка."},
}

PRIMARY_SERVICE_IDS = ["dialog_service", "planner_llama", "telegram_runner", "ha_api", "source_selector", "qdrant", "llm_log_helper", "redis"]
DEPENDENCY_IDS = ["redis", "ha_api", "telegram_runner", "planner_llama", "source_selector", "qdrant", "llm_log_helper", "ollama", "config_db", "action_executor", "system_runtime_snapshot"]


def nav_items(active: str = "overview") -> list[dict[str, Any]]:
    return [
        {"id": "overview", "title": "Обзор", "href": ".", "active": active == "overview", "disabled": False},
        {"id": "regression", "title": "Тесты", "href": "regression", "active": active == "regression", "disabled": False},
        {"id": "environment", "title": "Окружение", "href": "#environment", "active": active == "environment", "disabled": True},
        {"id": "qdrant", "title": "Qdrant", "href": "#qdrant", "active": active == "qdrant", "disabled": True},
        {"id": "prompts", "title": "Промты", "href": "#prompts", "active": active == "prompts", "disabled": True},
        {"id": "analyzers", "title": "Анализаторы", "href": "#analyzers", "active": active == "analyzers", "disabled": True},
        {"id": "actions", "title": "Действия", "href": "#actions", "active": active == "actions", "disabled": True},
        {"id": "objects", "title": "Объекты", "href": "#objects", "active": active == "objects", "disabled": True},
        {"id": "database", "title": "База данных", "href": "#database", "active": active == "database", "disabled": True},
        {"id": "logs", "title": "Логи", "href": "#logs", "active": active == "logs", "disabled": True},
        {"id": "about", "title": "О системе", "href": "#about", "active": active == "about", "disabled": True},
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


async def post_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 90.0) -> tuple[bool, int | None, int, Any, str]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload or {})
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            data: Any = response.json()
        except Exception:
            data = response.text[:2000]
        return 200 <= response.status_code < 300, response.status_code, elapsed_ms, data, ""
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
        return {**payload, "hdc_version": APP_VERSION, "dialog_service_url": dialog_service_url, "options": public_options(options), "summary": summary, "incidents": payload.get("incidents") or [], "recommendations": payload.get("recommendations") or [], "action_blocks": payload.get("action_blocks") or [], "checks": checks}
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
    return {"disabled": "Отключено", "not_configured": "Не настроено", "not_found": "Не найдено", "stale": "Устарело"}.get(str(check.get("status") or ""), "Не проверялось")


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
    for check_id in PRIMARY_SERVICE_IDS:
        meta = SERVICE_CARD_MAP[check_id]
        check = by_id.get(check_id)
        if not check:
            continue
        details = check.get("details") or {}
        elapsed_ms = details.get("elapsed_ms")
        cards.append({"id": check_id, "title": meta["title"], "icon": meta["icon"], "description": meta["description"], "label": status_label(check), "status": check.get("status") or "unknown", "class": state_class(check), "elapsed_ms": f"{elapsed_ms} мс" if elapsed_ms is not None else "не измеряется", "message": check.get("message") or "", "details": details})
    return cards


def build_summary_tiles(diagnostics: dict[str, Any], recommendations_visible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = diagnostics.get("summary") or {}
    total = int(summary.get("total") or 0)
    ok = int(summary.get("ok") or 0)
    availability = round((ok / total) * 100, 1) if total else 0
    return [
        {"label": "OK", "value": ok, "hint": f"из {total} проверок"},
        {"label": "Ошибки", "value": int(summary.get("failed") or 0), "hint": "критичные сбои"},
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
        elapsed_ms = details.get("elapsed_ms")
        rows.append({"title": SERVICE_CARD_MAP.get(check_id, {}).get("title", check.get("title", check_id)), "label": status_label(check), "class": state_class(check), "elapsed_ms": f"{elapsed_ms} мс" if elapsed_ms is not None else "не измеряется", "error": check.get("message") or "—", "recommendation": (rec_by_id.get(check_id) or {}).get("text") or "Нет рекомендаций"})
    return rows


def build_view_model(diagnostics: dict[str, Any]) -> dict[str, Any]:
    checks = diagnostics.get("checks") or []
    incidents = diagnostics.get("incidents") or []
    recommendations_visible = visible_recommendations(diagnostics.get("recommendations") or [])
    status = diagnostics.get("status") or "unknown"
    summary = diagnostics.get("summary") or {}
    has_problem = bool(summary.get("failed") or summary.get("incidents") or incidents)
    if status == "ok" and not has_problem:
        overall = {"class": "ok", "title": "Система работает нормально", "description": "Все ключевые сервисы доступны и функционируют в штатном режиме.", "label": "здорово"}
    elif status == "degraded":
        overall = {"class": "warning", "title": "Система работает с предупреждениями", "description": "Есть некритичные проблемы или устаревшие данные, требующие внимания.", "label": "предупреждение"}
    else:
        overall = {"class": "bad", "title": "Система работает с ошибками", "description": "Одна или несколько ключевых зависимостей недоступны.", "label": "ошибка"}
    return {"nav_items": nav_items("overview"), "overall": overall, "updated_at": format_time(diagnostics.get("generated_at")), "updated_at_full": format_dt(diagnostics.get("generated_at")), "service_cards": build_service_cards(checks), "summary_tiles": build_summary_tiles(diagnostics, recommendations_visible), "snapshot": build_snapshot(checks), "dependency_rows": build_dependency_rows(checks, recommendations_visible), "incidents": incidents, "recommendations": recommendations_visible, "recommendations_empty_text": "Действий не требуется: все диагностические проверки зелёные.", "action_blocks": diagnostics.get("action_blocks") or [], "checks": checks, "raw": diagnostics}


def regression_result_view(row: dict[str, Any]) -> dict[str, Any]:
    excerpt = row.get("response_excerpt") if isinstance(row.get("response_excerpt"), dict) else {}
    validated_plan = excerpt.get("validated_plan") if isinstance(excerpt.get("validated_plan"), dict) else {}
    model_call = excerpt.get("model_call") if isinstance(excerpt.get("model_call"), dict) else {}

    analyzer_ids = validated_plan.get("selected_analyzer_ids") or []
    if not isinstance(analyzer_ids, list):
        analyzer_ids = []

    runtime = excerpt.get("planner_runtime") or model_call.get("runtime") or "—"
    plan_type = validated_plan.get("plan_type") or "—"

    route_confidence = ""
    if runtime == "source_selector_route":
        best_positive_score = model_call.get("best_positive_score")
        candidate_score = model_call.get("candidate_score")
        if best_positive_score is not None or candidate_score is not None:
            route_confidence = f"positive={best_positive_score}; score={candidate_score}"

    return {
        **row,
        "planner_runtime": runtime,
        "plan_type": plan_type,
        "analyzer_ids": ", ".join(str(item) for item in analyzer_ids) if analyzer_ids else "—",
        "route_confidence": route_confidence,
    }


def build_regression_view(payload: dict[str, Any] | None, cases: dict[str, Any], group: str | None = None, error: str = "") -> dict[str, Any]:
    summary = (payload or {}).get("summary") or {}
    results = [regression_result_view(item) for item in ((payload or {}).get("results") or [])]
    return {
        "nav_items": nav_items("regression"),
        "group": group or "",
        "summary": summary,
        "results": results,
        "raw": payload or {},
        "cases": cases,
        "error": error,
        "updated_at": format_time(datetime.now(timezone.utc).isoformat()),
        "safe_note": "Эти тесты безопасны: они не вызывают /admin/actions/execute и не выполняют действия в Home Assistant.",
        "planner_note": "Planner regression теперь включает защитные проверки route-card и может выполняться 3–5 минут.",
    }


async def fetch_regression_cases(dialog_service_url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for group in ("core", "planner"):
        ok, status_code, elapsed_ms, payload, error = await get_json(f"{dialog_service_url}/admin/regression/cases?group={group}", timeout=15.0)
        out[group] = {"ok": ok, "status_code": status_code, "elapsed_ms": elapsed_ms, "payload": payload if ok else None, "error": error}
    return out


async def run_regression_group(group: str) -> tuple[dict[str, Any] | None, str]:
    if group not in {"core", "planner"}:
        return None, "Недопустимая группа тестов."
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    timeout = 30.0 if group == "core" else 360.0
    ok, status_code, elapsed_ms, payload, error = await post_json(f"{dialog_service_url}/admin/regression/run?group={group}", timeout=timeout)
    if ok and isinstance(payload, dict):
        payload.setdefault("elapsed_ms", elapsed_ms)
        return payload, ""
    return {"summary": {"total": 0, "passed": 0, "failed": 1, "elapsed_ms": elapsed_ms}, "results": [], "status_code": status_code}, error or f"HTTP {status_code}"


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


@app.get("/regression", response_class=HTMLResponse)
async def regression(request: Request) -> HTMLResponse:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    cases = await fetch_regression_cases(dialog_service_url)
    view = build_regression_view(None, cases)
    return templates.TemplateResponse(request, "regression.html", {"view": view, "options": public_options(options), "hdc_version": APP_VERSION})


@app.post("/regression/run/{group}", response_class=HTMLResponse)
async def regression_run(request: Request, group: str) -> HTMLResponse:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    cases = await fetch_regression_cases(dialog_service_url)
    payload, error = await run_regression_group(group)
    view = build_regression_view(payload, cases, group=group, error=error)
    return templates.TemplateResponse(request, "regression.html", {"view": view, "options": public_options(options), "hdc_version": APP_VERSION})
