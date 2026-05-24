# Documentation

## Purpose

Home Dialog Console (HDC) is a diagnostics and management UI for a local smart-home dialog runtime.

The MVP is intentionally small: it only checks its own health and `dialog-service /health`.

## Installation

1. Add this repository to the Home Assistant add-on store:

```text
https://github.com/alexsudakov/home-dialog-console
```

2. Install the `Home Dialog Console` add-on.
3. Set `dialog_service_url` in add-on settings.
4. Start the add-on.
5. Open the HDC panel from the Home Assistant sidebar.

## Development notes

For local development, copy the `home-dialog-console` folder into Home Assistant local add-ons and rebuild the add-on.

The add-on listens on port `8099` inside the container.

## Security boundary

HDC must not bypass the backend safety layer. Any future action execution must be delegated to `dialog-service` Action Executor.
