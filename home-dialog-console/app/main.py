from __future__ import annotations

import json
import logging
import os
import platform
import socket
import sys
import time
from urllib.parse import parse_qs
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_VERSION = "0.1.38"
CONFIG_PATH = Path("/data/options.json")
DEFAULT_DIALOG_SERVICE_URL = "http://127.0.0.1:8090"
DEFAULT_RETRIEVAL_SERVICE_URL = "http://192.168.1.138:8085"
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

PROTECTED_SOURCE_CARD_IDS = {
    "state_query",
    "system_health_summary",
    "house_events_summary",
    "people_history",
    "kitchen_hood_reasoning",
    "toilet_light_delay",
    "entity_inventory_query",
}


def is_protected_source_card(source_id: str, source: dict[str, Any] | None = None) -> bool:
    source = source or {}
    sid = str(source.get("source_id") or source_id)
    if sid in PROTECTED_SOURCE_CARD_IDS:
        return True

    # Временная защита до появления явного поля protected=true в карточке.
    if source.get("protected") is True:
        return True

    return False


def nav_items(active: str = "overview") -> list[dict[str, Any]]:
    return [
        {"id": "overview", "title": "Обзор", "href": ".", "active": active == "overview", "disabled": False},
        {"id": "regression", "title": "Тесты", "href": "regression", "active": active == "regression", "disabled": False},
        {"id": "environment", "title": "Окружение", "href": "environment", "active": active == "environment", "disabled": False},
        {"id": "config", "title": "Конфиг", "href": "config", "active": active == "config", "disabled": False},
        {"id": "qdrant", "title": "Qdrant", "href": "qdrant", "active": active == "qdrant", "disabled": False},
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

logger = logging.getLogger("home_dialog_console.regression")


def load_options() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {
            "dialog_service_url": os.getenv("DIALOG_SERVICE_URL", DEFAULT_DIALOG_SERVICE_URL),
            "retrieval_service_url": os.getenv("RETRIEVAL_SERVICE_URL", DEFAULT_RETRIEVAL_SERVICE_URL),
            "log_level": os.getenv("LOG_LEVEL", "info"),
        }
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {
        "dialog_service_url": data.get("dialog_service_url") or DEFAULT_DIALOG_SERVICE_URL,
        "retrieval_service_url": data.get("retrieval_service_url") or DEFAULT_RETRIEVAL_SERVICE_URL,
        "log_level": data.get("log_level") or "info",
    }


def public_options(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "dialog_service_url": options.get("dialog_service_url"),
        "retrieval_service_url": options.get("retrieval_service_url"),
        "log_level": options.get("log_level"),
    }


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


async def put_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 90.0) -> tuple[bool, int | None, int, Any, str]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.put(url, json=payload or {})
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            data: Any = response.json()
        except Exception:
            data = response.text[:2000]
        return 200 <= response.status_code < 300, response.status_code, elapsed_ms, data, ""
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return False, None, elapsed_ms, None, f"{type(exc).__name__}: {exc or 'no details'}"


async def delete_json(url: str, timeout: float = 90.0) -> tuple[bool, int | None, int, Any, str]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.delete(url)
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


def path_state(path: Path) -> str:
    try:
        if path.exists():
            return "есть"
        return "нет"
    except Exception:
        return "нет данных"


def build_environment_view() -> dict[str, Any]:
    options = load_options()
    dialog_service_url = str(options.get("dialog_service_url") or DEFAULT_DIALOG_SERVICE_URL)
    now_utc = datetime.now(timezone.utc)
    local_now = datetime.now().astimezone()

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "нет данных"

    try:
        cwd = str(Path.cwd())
    except Exception:
        cwd = "нет данных"

    sections = [
        {
            "title": "HDC add-on",
            "description": "Локальное окружение панели управления.",
            "rows": [
                {"name": "Версия HDC", "value": APP_VERSION, "hint": "версия кода add-on"},
                {"name": "Режим", "value": "read-only", "hint": "страница не выполняет действий в Home Assistant"},
                {"name": "Hostname", "value": hostname, "hint": "имя контейнера"},
                {"name": "Рабочий каталог", "value": cwd, "hint": "текущий каталог процесса"},
                {"name": "Каталог приложения", "value": str(BASE_DIR), "hint": "путь до app"},
            ],
        },
        {
            "title": "Python runtime",
            "description": "Версия Python и базовая информация о платформе.",
            "rows": [
                {"name": "Python", "value": python_version, "hint": "версия интерпретатора"},
                {"name": "Implementation", "value": platform.python_implementation(), "hint": "реализация Python"},
                {"name": "Platform", "value": platform.platform(), "hint": "платформа контейнера"},
                {"name": "Machine", "value": platform.machine(), "hint": "архитектура"},
            ],
        },
        {
            "title": "Настройки add-on",
            "description": "Публичные настройки без секретов и токенов.",
            "rows": [
                {"name": "dialog-service URL", "value": dialog_service_url, "hint": "адрес, настроенный в options"},
                {"name": "log_level", "value": str(options.get("log_level") or "info"), "hint": "уровень логирования"},
                {"name": "options.json", "value": str(CONFIG_PATH), "hint": f"файл: {path_state(CONFIG_PATH)}"},
                {"name": "DEFAULT_DIALOG_SERVICE_URL", "value": DEFAULT_DIALOG_SERVICE_URL, "hint": "значение по умолчанию"},
            ],
        },
        {
            "title": "Время контейнера",
            "description": "Базовая информация о времени HDC. Проверка NTP для CT101/HA будет отдельной задачей.",
            "rows": [
                {"name": "UTC", "value": now_utc.isoformat(), "hint": "текущее UTC-время HDC"},
                {"name": "Local", "value": local_now.isoformat(), "hint": "локальное время контейнера"},
                {"name": "NTP status", "value": "не проверяется", "hint": "пока только отображение времени контейнера"},
            ],
        },
        {
            "title": "Безопасность отображения",
            "description": "Этот экран не должен раскрывать секреты.",
            "rows": [
                {"name": "Секреты", "value": "не выводятся", "hint": "токены, пароли и ключи не показываются"},
                {"name": "Действия HA", "value": "не выполняются", "hint": "экран только читает локальные сведения HDC"},
                {"name": "Health-проверки", "value": "на главной странице", "hint": "раздел не дублирует диагностику сервисов"},
            ],
        },
    ]

    return {
        "nav_items": nav_items("environment"),
        "updated_at": format_time(now_utc.isoformat()),
        "updated_at_full": format_dt(now_utc.isoformat()),
        "summary_tiles": [
            {"label": "HDC", "value": APP_VERSION, "hint": "версия"},
            {"label": "Python", "value": python_version, "hint": "runtime"},
            {"label": "Config", "value": path_state(CONFIG_PATH), "hint": "options.json"},
            {"label": "Mode", "value": "read-only", "hint": "без действий"},
        ],
        "sections": sections,
        "options": public_options(options),
        "raw": {
            "app_version": APP_VERSION,
            "hostname": hostname,
            "python_version": python_version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "base_dir": str(BASE_DIR),
            "config_path": str(CONFIG_PATH),
            "config_exists": CONFIG_PATH.exists(),
            "dialog_service_url": dialog_service_url,
            "log_level": options.get("log_level"),
            "utc_now": now_utc.isoformat(),
            "local_now": local_now.isoformat(),
        },
    }


def mask_config_value(name: str, value: Any) -> str:
    key = str(name or "").lower()
    if any(part in key for part in ["token", "secret", "password", "passwd", "api_key", "apikey", "key"]):
        if value in [None, "", False]:
            return "не задано"
        return "******"
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def flatten_config(prefix: str, value: Any, depth: int = 0, max_depth: int = 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if isinstance(value, dict) and depth < max_depth:
        for key in sorted(value.keys()):
            name = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(flatten_config(name, value.get(key), depth + 1, max_depth))
        return rows

    if isinstance(value, list):
        if len(value) <= 8 and all(not isinstance(item, (dict, list)) for item in value):
            rows.append({"name": prefix, "value": mask_config_value(prefix, value), "hint": "list"})
        else:
            rows.append({"name": prefix, "value": f"list[{len(value)}]", "hint": "список скрыт, подробности в Raw JSON"})
        return rows

    rows.append({"name": prefix, "value": mask_config_value(prefix, value), "hint": type(value).__name__})
    return rows


def find_check(checks: list[dict[str, Any]], check_id: str) -> dict[str, Any]:
    for check in checks:
        if str(check.get("id") or "") == check_id:
            return check
    return {}


def check_config_rows(check: dict[str, Any]) -> list[dict[str, Any]]:
    if not check:
        return [{"name": "status", "value": "нет данных", "hint": "check не найден"}]

    rows = [
        {"name": "id", "value": check.get("id") or "—", "hint": "check id"},
        {"name": "title", "value": check.get("title") or "—", "hint": "название"},
        {"name": "ok", "value": str(check.get("ok")), "hint": "результат проверки"},
        {"name": "status", "value": check.get("status") or "—", "hint": "статус"},
        {"name": "message", "value": check.get("message") or "—", "hint": "последнее сообщение"},
    ]

    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    for row in flatten_config("details", details, max_depth=2):
        if len(str(row.get("value") or "")) > 500:
            row["value"] = str(row["value"])[:500] + "…"
        rows.append(row)

    return rows


async def build_config_view() -> dict[str, Any]:
    options = load_options()
    diagnostics = await build_diagnostics()
    checks = diagnostics.get("checks") if isinstance(diagnostics.get("checks"), list) else []

    dialog_service_url = str(options.get("dialog_service_url") or DEFAULT_DIALOG_SERVICE_URL).rstrip("/")
    retrieval_service_url = str(options.get("retrieval_service_url") or DEFAULT_RETRIEVAL_SERVICE_URL).rstrip("/")

    hdc_rows = [
        {"name": "APP_VERSION", "value": APP_VERSION, "hint": "версия HDC"},
        {"name": "CONFIG_PATH", "value": str(CONFIG_PATH), "hint": f"options.json: {path_state(CONFIG_PATH)}"},
        {"name": "dialog_service_url", "value": dialog_service_url, "hint": "из options/env/default"},
        {"name": "retrieval_service_url", "value": retrieval_service_url, "hint": "из options/env/default"},
        {"name": "log_level", "value": str(options.get("log_level") or "info"), "hint": "уровень логирования"},
    ]

    diagnostics_rows = [
        {"name": "service", "value": diagnostics.get("service") or "—", "hint": "diagnostics service"},
        {"name": "version", "value": diagnostics.get("version") or diagnostics.get("backend_version") or "—", "hint": "версия dialog-service, если есть"},
        {"name": "status", "value": diagnostics.get("status") or "—", "hint": "общий статус"},
        {"name": "env", "value": diagnostics.get("env") or "—", "hint": "окружение"},
        {"name": "generated_at", "value": format_dt(diagnostics.get("generated_at")), "hint": "время формирования diagnostics"},
        {"name": "checks_count", "value": str(len(checks)), "hint": "число diagnostics checks"},
    ]

    source_selector_check = find_check(checks, "source_selector")
    qdrant_check = find_check(checks, "qdrant")
    config_db_check = find_check(checks, "config_db")
    action_executor_check = find_check(checks, "action_executor")

    sections = [
        {
            "title": "HDC options",
            "description": "Публичные настройки HDC без секретов.",
            "rows": hdc_rows,
        },
        {
            "title": "dialog-service diagnostics",
            "description": "Сводка, которую HDC получает от dialog-service.",
            "rows": diagnostics_rows,
        },
        {
            "title": "Source Selector",
            "description": "Настройки и состояние Source Selector из diagnostics check.",
            "rows": check_config_rows(source_selector_check),
        },
        {
            "title": "Qdrant",
            "description": "Настройки и состояние Qdrant из diagnostics check.",
            "rows": check_config_rows(qdrant_check),
        },
        {
            "title": "Config DB",
            "description": "Состояние SQLite config DB из diagnostics check.",
            "rows": check_config_rows(config_db_check),
        },
        {
            "title": "Action Executor",
            "description": "Состояние безопасного исполнителя действий.",
            "rows": check_config_rows(action_executor_check),
        },
    ]

    return {
        "nav_items": nav_items("config"),
        "updated_at": format_time(datetime.now(timezone.utc).isoformat()),
        "updated_at_full": format_dt(datetime.now(timezone.utc).isoformat()),
        "summary_tiles": [
            {"label": "Mode", "value": "read-only", "hint": "без изменения настроек"},
            {"label": "Checks", "value": str(len(checks)), "hint": "diagnostics"},
            {"label": "HDC", "value": APP_VERSION, "hint": "версия"},
            {"label": "Secrets", "value": "masked", "hint": "секреты скрыты"},
        ],
        "sections": sections,
        "raw": {
            "options": public_options(options),
            "diagnostics": diagnostics,
            "checks": checks,
        },
    }


def lines_to_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def parse_json_list(value: Any) -> list[Any]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return lines_to_list(text)


def source_card_form_payload(form: Any, source_id: str) -> dict[str, Any]:
    enabled_text = str(form.get("enabled") or "true").lower()
    return {
        "source_id": source_id,
        "planner_id": str(form.get("planner_id") or source_id).strip(),
        "group": str(form.get("group") or "default").strip(),
        "enabled": enabled_text in ["1", "true", "yes", "on"],
        "title": str(form.get("title") or source_id).strip(),
        "short": str(form.get("short") or "").strip(),
        "full": str(form.get("full") or "").strip(),
        "card_kind": str(form.get("card_kind") or "source").strip(),
        "route_id": str(form.get("route_id") or "").strip() or None,
        "analyzer_id": str(form.get("analyzer_id") or "").strip() or None,
        "plan_type": str(form.get("plan_type") or "").strip() or None,
        "positive_examples": lines_to_list(form.get("positive_examples")),
        "negative_examples": lines_to_list(form.get("negative_examples")),
        "required_sources": lines_to_list(form.get("required_sources")),
        "optional_sources": lines_to_list(form.get("optional_sources")),
        "output_shape": lines_to_list(form.get("output_shape")),
        "selected_analyzers": parse_json_list(form.get("selected_analyzers_json")),
    }


def compact_details(details: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(details.keys()):
        value = details.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False)
        else:
            value_text = str(value)
        if len(value_text) > 500:
            value_text = value_text[:500] + "…"
        rows.append({"name": key, "value": value_text})
    return rows


def audit_change_summary(item: dict[str, Any]) -> str:
    before = item.get("before") if isinstance(item.get("before"), dict) else {}
    after = item.get("after") if isinstance(item.get("after"), dict) else {}

    if item.get("action") == "delete":
        title = before.get("title") or before.get("source_id") or ""
        return f"Удалена карточка: {title}" if title else "Карточка удалена."

    if item.get("action") == "create":
        title = after.get("title") or after.get("source_id") or ""
        return f"Создана карточка: {title}" if title else "Карточка создана."

    changed: list[str] = []
    for field in ("enabled", "title", "short", "full", "card_kind", "route_id", "analyzer_id", "plan_type"):
        if before.get(field) != after.get(field):
            changed.append(field)

    for field in ("positive_examples", "negative_examples", "required_sources", "optional_sources", "output_shape", "selected_analyzers"):
        if before.get(field) != after.get(field):
            changed.append(field)

    if changed:
        return "Изменены поля: " + ", ".join(changed[:8])

    return "Изменение записано."


async def fetch_source_card_audit(retrieval_service_url: str, source_id: str, limit: int = 10) -> dict[str, Any]:
    ok, status_code, elapsed_ms, payload, error = await get_json(
        f"{retrieval_service_url}/source/cards/{source_id}/audit?limit={limit}",
        timeout=20.0,
    )

    data = payload if isinstance(payload, dict) else {}
    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    items: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["created_at_display"] = format_dt(row.get("created_at"))
        row["summary"] = audit_change_summary(row)
        items.append(row)

    return {
        "ok": ok and bool(data.get("ok", ok)),
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": error or (data.get("detail") if isinstance(data, dict) else "") or data.get("error") or "",
        "source_id": data.get("source_id") or source_id,
        "limit": data.get("limit") or limit,
        "items": items,
    }


def check_card_from_diagnostics(checks: list[dict[str, Any]], check_id: str) -> dict[str, Any]:
    check = check_by_id(checks).get(check_id) or {}
    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    found = bool(check)
    return {
        "id": check_id,
        "title": SERVICE_CARD_MAP.get(check_id, {}).get("title", check.get("title", check_id)),
        "label": status_label(check) if found else "Не найдено",
        "class": state_class(check) if found else "neutral",
        "status": check.get("status") or "not_found",
        "message": check.get("message") or ("" if found else "Проверка не найдена в diagnostics summary."),
        "details": details,
        "details_rows": compact_details(details),
    }


def nested_response(details: dict[str, Any]) -> dict[str, Any]:
    response = details.get("response")
    return response if isinstance(response, dict) else {}


def compact_matched_chunks(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk in candidate.get("matched_chunks") or []:
        if not isinstance(chunk, dict):
            continue
        rows.append({
            "chunk_type": chunk.get("chunk_type") or "—",
            "score": chunk.get("score"),
            "weighted": chunk.get("weighted"),
            "text": chunk.get("text") or "",
        })
    return rows


def compact_source_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    source_selector = data.get("source_selector") if isinstance(data.get("source_selector"), dict) else {}
    candidates = source_selector.get("candidates") if isinstance(source_selector.get("candidates"), list) else []

    rows: list[dict[str, Any]] = []
    for candidate in candidates[:5]:
        if not isinstance(candidate, dict):
            continue
        rows.append({
            "source_id": candidate.get("source_id") or "—",
            "title": candidate.get("title") or "—",
            "card_kind": candidate.get("card_kind") or "—",
            "group": candidate.get("group") or "—",
            "planner_id": candidate.get("planner_id") or "—",
            "route_id": candidate.get("route_id") or "—",
            "analyzer_id": candidate.get("analyzer_id") or "—",
            "plan_type": candidate.get("plan_type") or "—",
            "score": candidate.get("score"),
            "required_sources": candidate.get("required_sources") or [],
            "optional_sources": candidate.get("optional_sources") or [],
            "matched_chunks": compact_matched_chunks(candidate),
        })
    return rows


async def qdrant_route_probe(dialog_service_url: str, question: str, expected: str) -> dict[str, Any]:
    url = f"{dialog_service_url}/debug/planner/route-shortcut"
    ok, status_code, elapsed_ms, payload, error = await post_json(url, {"question": question}, timeout=20.0)

    data = payload if isinstance(payload, dict) else {}
    accepted = data.get("accepted")
    reject_reason = data.get("reject_reason")
    route_id = data.get("route_id")
    plan_type = data.get("plan_type")
    best_positive_score = data.get("best_positive_score")
    candidate_score = data.get("candidate_score")

    expected_ok = False
    if expected == "accepted":
        expected_ok = ok and accepted is True
    elif expected == "rejected":
        expected_ok = ok and accepted is False

    source_selector = data.get("source_selector") if isinstance(data.get("source_selector"), dict) else {}

    return {
        "question": question,
        "expected": expected,
        "ok": expected_ok,
        "transport_ok": ok,
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "accepted": accepted,
        "reject_reason": reject_reason or "—",
        "route_id": route_id or "—",
        "plan_type": plan_type or "—",
        "best_positive_score": best_positive_score,
        "candidate_score": candidate_score,
        "error": error,
        "collection": source_selector.get("collection") or "нет данных",
        "model": source_selector.get("model") or "нет данных",
        "source_selector_elapsed_ms": source_selector.get("elapsed_ms"),
        "source_candidates": compact_source_candidates(data),
        "raw": data,
    }


async def build_qdrant_view(reindex_result: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = await build_diagnostics()
    checks = diagnostics.get("checks") or []
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    retrieval_service_url = str(options["retrieval_service_url"]).rstrip("/")

    qdrant_card = check_card_from_diagnostics(checks, "qdrant")
    source_selector_card = check_card_from_diagnostics(checks, "source_selector")

    cards_ok, cards_status, cards_elapsed_ms, cards_payload, cards_error = await get_json(
        f"{retrieval_service_url}/source/cards",
        timeout=20.0,
    )
    cards_data = cards_payload if isinstance(cards_payload, dict) else {}
    source_cards = cards_data.get("sources") if isinstance(cards_data.get("sources"), list) else []

    probes = [
        await qdrant_route_probe(dialog_service_url, "Что с вытяжкой на кухне?", "accepted"),
        await qdrant_route_probe(dialog_service_url, "Что с вентиляцией на кухне?", "rejected"),
    ]

    qdrant_details = qdrant_card.get("details") or {}
    source_details = source_selector_card.get("details") or {}
    source_response = nested_response(source_details)

    collection = (
        cards_data.get("collection")
        or source_response.get("source_collection")
        or source_response.get("collection")
        or source_details.get("source_collection")
        or source_details.get("collection")
        or qdrant_details.get("expected_collection")
        or qdrant_details.get("source_collection")
        or qdrant_details.get("collection")
        or next((probe.get("collection") for probe in probes if probe.get("collection") not in [None, "", "нет данных"]), None)
        or "нет данных"
    )

    model = (
        cards_data.get("model")
        or source_response.get("embedding_model")
        or source_response.get("model")
        or source_details.get("embedding_model")
        or source_details.get("model")
        or qdrant_details.get("embedding_model")
        or qdrant_details.get("model")
        or next((probe.get("model") for probe in probes if probe.get("model") not in [None, "", "нет данных"]), None)
        or "нет данных"
    )

    ok_count = sum(1 for item in [qdrant_card, source_selector_card] if item.get("class") == "ok")
    probe_ok_count = sum(1 for item in probes if item.get("ok"))

    return {
        "nav_items": nav_items("qdrant"),
        "updated_at": format_time(diagnostics.get("generated_at") or datetime.now(timezone.utc).isoformat()),
        "updated_at_full": format_dt(diagnostics.get("generated_at") or datetime.now(timezone.utc).isoformat()),
        "summary_tiles": [
            {"label": "Qdrant", "value": qdrant_card["label"], "hint": qdrant_card["status"]},
            {"label": "Source Selector", "value": source_selector_card["label"], "hint": source_selector_card["status"]},
            {"label": "Collection", "value": collection, "hint": "source cards"},
            {"label": "Cards", "value": str(len(source_cards)), "hint": "SQLite cards"},
            {"label": "Probes", "value": f"{probe_ok_count}/2", "hint": "route-card"},
        ],
        "cards": [qdrant_card, source_selector_card],
        "probes": probes,
        "source_cards": source_cards,
        "source_cards_status": {
            "ok": cards_ok and bool(cards_data.get("ok", cards_ok)),
            "status_code": cards_status,
            "elapsed_ms": cards_elapsed_ms,
            "error": cards_error or cards_data.get("error") or "",
            "cards_path": cards_data.get("cards_path") or "нет данных",
            "sources_count": cards_data.get("sources_count") or len(source_cards),
            "chunks_count": cards_data.get("chunks_count"),
            "version": cards_data.get("version") or "нет данных",
        },
        "reindex_result": reindex_result,
        "collection": collection,
        "model": model,
        "dialog_service_url": dialog_service_url,
        "retrieval_service_url": retrieval_service_url,
        "overall": {
            "class": "ok" if ok_count == 2 and probe_ok_count == 2 else "warning",
            "title": "Qdrant и Source Selector доступны" if ok_count == 2 and probe_ok_count == 2 else "Есть предупреждения по Qdrant / Source Selector",
            "description": "Раздел проверяет Qdrant через diagnostics summary и безопасные route-card проверки.",
            "label": "read-only",
        },
        "raw": {
            "diagnostics": diagnostics,
            "qdrant": qdrant_card,
            "source_selector": source_selector_card,
            "probes": probes,
            "source_cards": source_cards,
            "source_cards_status": cards_data,
            "collection": collection,
            "model": model,
            "retrieval_service_url": retrieval_service_url,
        },
    }


RESOLVER_EXAMPLES = [
    "Что с вытяжкой на кухне?",
    "Была ли какая-то тревога дома?",
    "Где сейчас Ира?",
    "Где была Ира сегодня?",
    "Когда Ира ушла?",
    "включи свет в ванной",
    "что со светом в туалете",
]


def compact_source_select_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    rows: list[dict[str, Any]] = []

    for item in candidates[:10]:
        if not isinstance(item, dict):
            continue
        rows.append({
            "source_id": item.get("source_id") or "—",
            "card_kind": item.get("card_kind") or "—",
            "route_id": item.get("route_id") or "—",
            "analyzer_id": item.get("analyzer_id") or "—",
            "plan_type": item.get("plan_type") or "—",
            "score": item.get("score"),
            "best_positive_score": item.get("best_positive_score"),
            "title": item.get("title") or "—",
        })

    return rows


def resolver_empty_result() -> dict[str, Any]:
    return {
        "question": "",
        "has_result": False,
        "route": {},
        "source": {},
        "route_error": "",
        "source_error": "",
        "source_candidates": [],
        "raw": {},
    }


async def run_resolver_check(dialog_service_url: str, question: str) -> dict[str, Any]:
    question_clean = str(question or "").strip()
    if not question_clean:
        result = resolver_empty_result()
        result["route_error"] = "Введите фразу для проверки."
        return result

    route_ok, route_status, route_elapsed_ms, route_payload, route_error = await post_json(
        f"{dialog_service_url}/debug/planner/route-shortcut",
        {"question": question_clean},
        timeout=30.0,
    )

    source_ok, source_status, source_elapsed_ms, source_payload, source_error = await post_json(
        f"{dialog_service_url}/debug/source/select",
        {"question": question_clean, "top_k": 5, "debug": True},
        timeout=30.0,
    )

    route_data = route_payload if isinstance(route_payload, dict) else {}
    source_data = source_payload if isinstance(source_payload, dict) else {}

    top1 = {}
    candidates = source_data.get("candidates") if isinstance(source_data.get("candidates"), list) else []
    if candidates and isinstance(candidates[0], dict):
        top1 = candidates[0]

    return {
        "question": question_clean,
        "has_result": True,
        "route": {
            "transport_ok": route_ok,
            "status_code": route_status,
            "elapsed_ms": route_elapsed_ms,
            "accepted": route_data.get("accepted"),
            "reject_reason": route_data.get("reject_reason") or "—",
            "source_id": route_data.get("source_id") or "—",
            "route_id": route_data.get("route_id") or "—",
            "analyzer_id": route_data.get("analyzer_id") or "—",
            "plan_type": route_data.get("plan_type") or "—",
            "best_positive_score": route_data.get("best_positive_score"),
            "candidate_score": route_data.get("candidate_score"),
        },
        "source": {
            "transport_ok": source_ok,
            "status_code": source_status,
            "elapsed_ms": source_elapsed_ms,
            "ok": source_data.get("ok"),
            "top_source_id": top1.get("source_id") or "—",
            "top_card_kind": top1.get("card_kind") or "—",
            "top_route_id": top1.get("route_id") or "—",
            "top_analyzer_id": top1.get("analyzer_id") or "—",
            "top_plan_type": top1.get("plan_type") or "—",
            "top_score": top1.get("score"),
            "top_title": top1.get("title") or "—",
            "collection": source_data.get("collection") or "—",
            "model": source_data.get("model") or "—",
        },
        "route_error": route_error,
        "source_error": source_error,
        "source_candidates": compact_source_select_candidates(source_data),
        "raw": {
            "route_shortcut": route_data,
            "source_select": source_data,
        },
    }


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


def build_regression_view(payload: dict[str, Any] | None, cases: dict[str, Any], group: str | None = None, error: str = "", resolver_result: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "planner_note": "Planner regression включает быстрые route-card проверки и обычно выполняется 1–2 минуты.",
        "resolver_examples": RESOLVER_EXAMPLES,
        "resolver_result": resolver_result or resolver_empty_result(),
    }


def regression_log_payload(group: str, payload: dict[str, Any] | None, error: str = "") -> dict[str, Any]:
    payload = payload or {}
    compact_results: list[dict[str, Any]] = []

    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue

        excerpt = row.get("response_excerpt") if isinstance(row.get("response_excerpt"), dict) else {}
        validated_plan = excerpt.get("validated_plan") if isinstance(excerpt.get("validated_plan"), dict) else {}
        model_call = excerpt.get("model_call") if isinstance(excerpt.get("model_call"), dict) else {}
        route_shortcut = excerpt.get("route_shortcut") if isinstance(excerpt.get("route_shortcut"), dict) else {}

        compact_results.append({
            "case_id": row.get("case_id"),
            "status": row.get("status"),
            "ok": row.get("ok"),
            "elapsed_ms": row.get("elapsed_ms"),
            "http_status": row.get("http_status"),
            "endpoint": row.get("endpoint"),
            "planner_runtime": excerpt.get("planner_runtime") or model_call.get("runtime"),
            "model": excerpt.get("model"),
            "plan_type": validated_plan.get("plan_type") or excerpt.get("plan_type") or route_shortcut.get("plan_type"),
            "selected_analyzer_ids": validated_plan.get("selected_analyzer_ids") or [],
            "route_id": model_call.get("route_id") or excerpt.get("route_id") or route_shortcut.get("route_id"),
            "route_accepted": excerpt.get("accepted") if "accepted" in excerpt else route_shortcut.get("accepted"),
            "route_reject_reason": excerpt.get("reject_reason") or route_shortcut.get("reject_reason"),
            "best_positive_score": excerpt.get("best_positive_score") or route_shortcut.get("best_positive_score"),
            "candidate_score": excerpt.get("candidate_score") or route_shortcut.get("candidate_score"),
            "failures": row.get("failures") or [],
            "error": row.get("error") or "",
        })

    return {
        "event": "regression_run_result",
        "group": group,
        "ok": bool(payload.get("ok")),
        "suite": payload.get("suite") or group,
        "base_url": payload.get("base_url"),
        "elapsed_ms": payload.get("elapsed_ms") or (payload.get("summary") or {}).get("elapsed_ms"),
        "summary": payload.get("summary") or {},
        "error": error,
        "results": compact_results,
    }


def log_regression_result(group: str, payload: dict[str, Any] | None, error: str = "") -> None:
    log_payload = regression_log_payload(group, payload, error=error)
    print(
        "REGRESSION_RUN_RESULT_JSON "
        + json.dumps(log_payload, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


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
    timeout = 30.0 if group == "core" else 180.0
    ok, status_code, elapsed_ms, payload, error = await post_json(f"{dialog_service_url}/admin/regression/run?group={group}", timeout=timeout)
    if ok and isinstance(payload, dict):
        payload.setdefault("elapsed_ms", elapsed_ms)
        log_regression_result(group, payload)
        return payload, ""

    fallback_payload = {
        "summary": {"total": 0, "passed": 0, "failed": 1, "elapsed_ms": elapsed_ms},
        "results": [],
        "status_code": status_code,
    }
    log_regression_result(group, fallback_payload, error=error or f"HTTP {status_code}")
    return fallback_payload, error or f"HTTP {status_code}"


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


@app.get("/environment", response_class=HTMLResponse)
async def environment(request: Request) -> HTMLResponse:
    view = build_environment_view()
    return templates.TemplateResponse(request, "environment.html", {"view": view, "options": view["options"], "hdc_version": APP_VERSION})


@app.get("/config", response_class=HTMLResponse)
async def config_browser(request: Request) -> HTMLResponse:
    view = await build_config_view()
    return templates.TemplateResponse(request, "config.html", {"view": view, "hdc_version": APP_VERSION})


@app.get("/qdrant", response_class=HTMLResponse)
async def qdrant(request: Request) -> HTMLResponse:
    view = await build_qdrant_view()
    return templates.TemplateResponse(request, "qdrant.html", {"view": view, "hdc_version": APP_VERSION})


@app.post("/qdrant/reindex", response_class=HTMLResponse)
async def qdrant_reindex(request: Request) -> HTMLResponse:
    options = load_options()
    retrieval_service_url = str(options["retrieval_service_url"]).rstrip("/")
    ok, status_code, elapsed_ms, payload, error = await post_json(
        f"{retrieval_service_url}/source/index",
        {},
        timeout=120.0,
    )
    data = payload if isinstance(payload, dict) else {}
    reindex_result = {
        "ok": ok and bool(data.get("ok", ok)),
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": error or data.get("error") or "",
        "payload": data,
    }
    view = await build_qdrant_view(reindex_result=reindex_result)
    return templates.TemplateResponse(request, "qdrant.html", {"view": view, "hdc_version": APP_VERSION})


@app.get("/qdrant/cards/{source_id}", response_class=HTMLResponse)
async def qdrant_card_view(request: Request, source_id: str) -> HTMLResponse:
    options = load_options()
    retrieval_service_url = str(options["retrieval_service_url"]).rstrip("/")

    ok, status_code, elapsed_ms, payload, error = await get_json(
        f"{retrieval_service_url}/source/cards/{source_id}",
        timeout=20.0,
    )

    data = payload if isinstance(payload, dict) else {}
    source = data.get("source") if isinstance(data.get("source"), dict) else {}

    view = {
        "nav_items": nav_items("qdrant"),
        "source_id": source_id,
        "source": source,
        "ok": ok and bool(data.get("ok", ok)),
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": error or ("" if source else "Карточка не найдена."),
        "save_result": None,
        "reindex_result": None,
        "toggle_result": None,
        "delete_result": None,
        "audit": await fetch_source_card_audit(retrieval_service_url, source_id),
        "protected": is_protected_source_card(source_id, source),
        "retrieval_service_url": retrieval_service_url,
        "updated_at": format_time(datetime.now(timezone.utc).isoformat()),
        "updated_at_full": format_dt(datetime.now(timezone.utc).isoformat()),
    }

    return templates.TemplateResponse(
        request,
        "qdrant_card.html",
        {"view": view, "hdc_version": APP_VERSION},
    )


@app.post("/qdrant/cards/{source_id}/save", response_class=HTMLResponse)
async def qdrant_card_save(request: Request, source_id: str) -> HTMLResponse:
    options = load_options()
    retrieval_service_url = str(options["retrieval_service_url"]).rstrip("/")
    body = await request.body()
    parsed_form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    form = {key: values[-1] if values else "" for key, values in parsed_form.items()}
    action = str(form.get("action") or "save")
    card_payload = source_card_form_payload(form, source_id)

    ok, status_code, elapsed_ms, payload, error = await put_json(
        f"{retrieval_service_url}/source/cards/{source_id}",
        card_payload,
        timeout=30.0,
    )

    data = payload if isinstance(payload, dict) else {}
    source = data.get("source") if isinstance(data.get("source"), dict) else card_payload

    reindex_result = None
    if ok and action == "save_reindex":
        ri_ok, ri_status_code, ri_elapsed_ms, ri_payload, ri_error = await post_json(
            f"{retrieval_service_url}/source/index",
            {},
            timeout=180.0,
        )
        ri_data = ri_payload if isinstance(ri_payload, dict) else {}
        reindex_result = {
            "ok": ri_ok and bool(ri_data.get("ok", ri_ok)),
            "status_code": ri_status_code,
            "elapsed_ms": ri_elapsed_ms,
            "payload": ri_data,
            "error": ri_error or (ri_data.get("error") if isinstance(ri_data, dict) else "") or "",
        }

    view = {
        "nav_items": nav_items("qdrant"),
        "source_id": source_id,
        "source": source,
        "ok": ok and bool(data.get("ok", ok)),
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": error or (data.get("detail") if isinstance(data, dict) else "") or "",
        "save_result": {
            "ok": ok,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "error": error or "",
        },
        "reindex_result": reindex_result,
        "toggle_result": None,
        "delete_result": None,
        "audit": await fetch_source_card_audit(retrieval_service_url, source_id),
        "protected": is_protected_source_card(source_id, source),
        "retrieval_service_url": retrieval_service_url,
        "updated_at": format_time(datetime.now(timezone.utc).isoformat()),
        "updated_at_full": format_dt(datetime.now(timezone.utc).isoformat()),
    }

    return templates.TemplateResponse(
        request,
        "qdrant_card.html",
        {"view": view, "hdc_version": APP_VERSION},
    )


@app.post("/qdrant/cards/{source_id}/toggle", response_class=HTMLResponse)
async def qdrant_card_toggle(request: Request, source_id: str) -> HTMLResponse:
    options = load_options()
    retrieval_service_url = str(options["retrieval_service_url"]).rstrip("/")

    body = await request.body()
    parsed_form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    form = {key: values[-1] if values else "" for key, values in parsed_form.items()}

    enabled_now = str(form.get("enabled") or "true").lower() in ["1", "true", "yes", "on"]
    endpoint = "disable" if enabled_now else "enable"

    ok, status_code, elapsed_ms, payload, error = await post_json(
        f"{retrieval_service_url}/source/cards/{source_id}/{endpoint}",
        {},
        timeout=30.0,
    )

    data = payload if isinstance(payload, dict) else {}
    source = data.get("source") if isinstance(data.get("source"), dict) else {}

    view = {
        "nav_items": nav_items("qdrant"),
        "source_id": source_id,
        "source": source,
        "ok": ok and bool(data.get("ok", ok)),
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": error or (data.get("detail") if isinstance(data, dict) else "") or "",
        "save_result": None,
        "reindex_result": None,
        "toggle_result": {
            "ok": ok and bool(data.get("ok", ok)),
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "action": endpoint,
            "error": error or "",
        },
        "delete_result": None,
        "audit": await fetch_source_card_audit(retrieval_service_url, source_id),
        "protected": is_protected_source_card(source_id, source),
        "retrieval_service_url": retrieval_service_url,
        "updated_at": format_time(datetime.now(timezone.utc).isoformat()),
        "updated_at_full": format_dt(datetime.now(timezone.utc).isoformat()),
    }

    return templates.TemplateResponse(
        request,
        "qdrant_card.html",
        {"view": view, "hdc_version": APP_VERSION},
    )


@app.post("/qdrant/cards/{source_id}/delete", response_class=HTMLResponse)
async def qdrant_card_delete(request: Request, source_id: str) -> HTMLResponse:
    options = load_options()
    retrieval_service_url = str(options["retrieval_service_url"]).rstrip("/")

    read_ok, read_status_code, read_elapsed_ms, read_payload, read_error = await get_json(
        f"{retrieval_service_url}/source/cards/{source_id}",
        timeout=20.0,
    )

    read_data = read_payload if isinstance(read_payload, dict) else {}
    source = read_data.get("source") if isinstance(read_data.get("source"), dict) else {}

    delete_result = {
        "ok": False,
        "status_code": read_status_code,
        "elapsed_ms": read_elapsed_ms,
        "error": read_error or "",
        "blocked": False,
    }

    if not read_ok or not source:
        delete_result["error"] = read_error or "Карточка не найдена."
    elif is_protected_source_card(source_id, source):
        delete_result["blocked"] = True
        delete_result["error"] = "Системную карточку нельзя удалить. Можно только включить или отключить."
    else:
        ok, status_code, elapsed_ms, payload, error = await delete_json(
            f"{retrieval_service_url}/source/cards/{source_id}",
            timeout=30.0,
        )
        data = payload if isinstance(payload, dict) else {}
        delete_result = {
            "ok": ok and bool(data.get("ok", ok)),
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "error": error or (data.get("detail") if isinstance(data, dict) else "") or "",
            "blocked": False,
        }
        if delete_result["ok"]:
            source = {}

    view = {
        "nav_items": nav_items("qdrant"),
        "source_id": source_id,
        "source": source,
        "ok": read_ok and bool(read_data.get("ok", read_ok)),
        "status_code": read_status_code,
        "elapsed_ms": read_elapsed_ms,
        "error": "",
        "save_result": None,
        "reindex_result": None,
        "toggle_result": None,
        "delete_result": delete_result,
        "audit": await fetch_source_card_audit(retrieval_service_url, source_id),
        "protected": is_protected_source_card(source_id, source),
        "retrieval_service_url": retrieval_service_url,
        "updated_at": format_time(datetime.now(timezone.utc).isoformat()),
        "updated_at_full": format_dt(datetime.now(timezone.utc).isoformat()),
    }

    return templates.TemplateResponse(
        request,
        "qdrant_card.html",
        {"view": view, "hdc_version": APP_VERSION},
    )


@app.get("/regression", response_class=HTMLResponse)
async def regression(request: Request) -> HTMLResponse:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    cases = await fetch_regression_cases(dialog_service_url)
    view = build_regression_view(None, cases)
    return templates.TemplateResponse(request, "regression.html", {"view": view, "options": public_options(options), "hdc_version": APP_VERSION})


@app.post("/regression/resolver/check", response_class=HTMLResponse)
async def regression_resolver_check(request: Request) -> HTMLResponse:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    cases = await fetch_regression_cases(dialog_service_url)

    body = await request.body()
    parsed_form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    form = {key: values[-1] if values else "" for key, values in parsed_form.items()}
    question = str(form.get("question") or "").strip()

    resolver_result = await run_resolver_check(dialog_service_url, question)
    view = build_regression_view(None, cases, resolver_result=resolver_result)
    return templates.TemplateResponse(request, "regression.html", {"view": view, "options": public_options(options), "hdc_version": APP_VERSION})


@app.post("/regression/run/{group}", response_class=HTMLResponse)
async def regression_run(request: Request, group: str) -> HTMLResponse:
    options = load_options()
    dialog_service_url = str(options["dialog_service_url"]).rstrip("/")
    cases = await fetch_regression_cases(dialog_service_url)
    payload, error = await run_regression_group(group)
    view = build_regression_view(payload, cases, group=group, error=error)
    return templates.TemplateResponse(request, "regression.html", {"view": view, "options": public_options(options), "hdc_version": APP_VERSION})
