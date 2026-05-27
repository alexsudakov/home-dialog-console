# Regression result logs

Дата фиксации: 2026-05-28

Home Dialog Console версии 0.1.24 пишет результаты запусков `Run core` и `Run planner` в журнал add-on.

Каждая запись имеет маркер `REGRESSION_RUN_RESULT_JSON` и содержит компактный JSON с итогом проверки.

## Состав записи

Верхний уровень:

- `event`
- `group`
- `ok`
- `suite`
- `base_url`
- `elapsed_ms`
- `summary`
- `error`
- `results`

Один элемент `results` содержит:

- `case_id`
- `endpoint`
- `status`
- `ok`
- `elapsed_ms`
- `http_status`
- `planner_runtime`
- `model`
- `plan_type`
- `selected_analyzer_ids`
- `route_id`
- `route_accepted`
- `route_reject_reason`
- `best_positive_score`
- `candidate_score`
- `failures`
- `error`

## Planner regression

Обычные planner-тесты используют endpoint `/debug/planner/plan`.

Защитные route-shortcut тесты используют endpoint `/debug/planner/route-shortcut`.

Для защитных тестов важны поля:

- `route_accepted=false`
- `route_reject_reason=positive_example_score_too_low`
- `best_positive_score`
- `candidate_score`

## Проверенная точка

Проверенная версия HDC: `0.1.24`.

Проверенный результат Planner regression:

- `total=11`
- `passed=11`
- `failed=0`
- `elapsed_ms≈89000`

Guard-тесты route-shortcut выполняются примерно за 30–40 мс и не вызывают LLM Planner.
