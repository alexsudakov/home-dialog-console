# Home Dialog Console add-on

Home Dialog Console is a Home Assistant add-on that provides a diagnostics UI for a local `dialog-service`.

## MVP scope

The first version shows:

- HDC health;
- configured `dialog-service` URL;
- `dialog-service /health` result;
- response time and error text when unavailable.

## Configuration

```yaml
dialog_service_url: "http://127.0.0.1:8090"
log_level: "info"
```

For a real installation, set `dialog_service_url` to the address reachable from the add-on container.

## Safety

HDC does not execute Home Assistant actions directly. Future action checks and execution must go through `dialog-service` and its Action Executor.
