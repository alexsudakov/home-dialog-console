# Home Dialog Console

Home Dialog Console (HDC) is a Home Assistant add-on for viewing diagnostics and control surfaces for a local `dialog-service` used in smart home dialog scenarios.

The first goal is a diagnostic screen that answers a simple question: what works, what is unavailable, and what needs attention.

## Current status

Early MVP skeleton.

## Add-on repository

This repository is structured as a Home Assistant add-on repository.

Add-on folder:

```text
home-dialog-console/
```

## Safety boundary

HDC is a management and diagnostics UI. It must not execute Home Assistant actions directly. Any future action checks or execution must go through `dialog-service` and its Action Executor.

## Private data

Do not commit private Home Assistant entity IDs, tokens, logs, family names, real local IP addresses, `.env` files, or household-specific configuration into this public repository.
