#!/usr/bin/env sh
set -eu

APP_SLUG="${APP_SLUG:-a4155af5_home_dialog_console}"

log() {
  printf '\n== %s ==\n' "$1"
}

log "Reload Home Assistant app store"
ha supervisor reload || true
ha store reload || true
ha apps reload

log "Current HDC app info"
ha apps info "$APP_SLUG" || true

log "Update HDC app"
ha apps update "$APP_SLUG"

log "Restart HDC app"
ha apps restart "$APP_SLUG"

log "HDC app info after update"
ha apps info "$APP_SLUG" || true

log "HDC logs"
ha apps logs "$APP_SLUG"
