# Home Dialog Console

Home Dialog Console (HDC) is a Home Assistant add-on for viewing diagnostics and control surfaces for a local `dialog-service` used in smart home dialog scenarios.

The first goal was a diagnostic screen that answers a simple question: what works, what is unavailable, and what needs attention. HDC now also includes tested control surfaces for regression checks and Source Selector cards.

## Current status

Current working version:

```text
HDC: 0.1.36
```

Implemented and checked:

- diagnostics overview for `dialog-service` and its key dependencies;
- environment page;
- regression/test page for safe HDC-friendly checks;
- Qdrant / Source Selector page;
- Source Selector cards list, search and filters;
- route-card probes for Source Selector behavior;
- Qdrant reindex action;
- Source Selector card page;
- edit and save Source Selector card fields through `retrieval-service`;
- save and reindex Qdrant;
- enable and disable Source Selector cards;
- protected delete for non-system Source Selector cards;
- source card audit display on the card page;
- Home Assistant Ingress-safe navigation and form actions after POST.

The Source Selector cards are edited through `retrieval-service`. The working storage is SQLite on the retrieval node, while Qdrant remains a search index.

## Add-on repository

This repository is structured as a Home Assistant add-on repository.

Add-on folder:

```text
home-dialog-console/
```

Main application files:

```text
home-dialog-console/app/main.py
home-dialog-console/app/templates/
home-dialog-console/app/templates/qdrant.html
home-dialog-console/app/templates/qdrant_card.html
home-dialog-console/config.yaml
home-dialog-console/Dockerfile
```

## Source Selector card safety

HDC can delete non-system Source Selector cards, but system cards are protected.

Currently protected cards:

```text
state_query
system_health_summary
house_events_summary
people_history
kitchen_hood_reasoning
toilet_light_delay
entity_inventory_query
```

Protected cards can still be edited, enabled and disabled.

## Retrieval service dependency

HDC expects `retrieval-service` to provide Source Selector card APIs:

```text
GET    /source/cards
GET    /source/cards/{source_id}
POST   /source/cards
PUT    /source/cards/{source_id}
POST   /source/cards/{source_id}/disable
POST   /source/cards/{source_id}/enable
DELETE /source/cards/{source_id}
GET    /source/cards/audit
GET    /source/cards/{source_id}/audit
POST   /source/index
```

The add-on should not store secrets in code. Runtime URLs must be supplied through add-on options, environment variables or Home Assistant configuration.

## Development notes

- Do not add new Python dependencies unless the add-on build has been checked.
- `python-multipart` is not currently required for the existing forms.
- Card forms currently parse `application/x-www-form-urlencoded` manually in `app/main.py`.
- After changing HDC code, update both `APP_VERSION` in `app/main.py` and `version` in `config.yaml` so Home Assistant Supervisor can see the add-on update.
- HDC must use relative links and Ingress-safe navigation. Avoid absolute links such as `/` inside the add-on UI.

## Safety boundary

HDC is a management and diagnostics UI. It must not execute Home Assistant actions directly. Any future action checks or execution must go through `dialog-service` and its Action Executor.

## Private data

Do not commit private Home Assistant entity IDs, tokens, logs, family names, real local IP addresses, `.env` files, or household-specific configuration into this public repository.
