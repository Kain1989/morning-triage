#!/usr/bin/env bash
# One-time setup for the morning-triage plugin: create a Playwright venv, scaffold .env,
# and guide the three one-time browser logins. Re-runnable and idempotent.
#
# Usage:
#   ./setup.sh            install deps + create .env   (then edit .env)
#   ./setup.sh --login    open the browsers for the one-time O365 + Zoom sign-in
#   ./setup.sh --check    run the preflight selftest
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"

# Load .env (if present) so MT_STATE_DIR etc. are honored.
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
STATE="${MT_STATE_DIR:-$HOME/.morning-triage}"
STATE="${STATE/#\~/$HOME}"
VENV="$STATE/venv"
PY="$VENV/bin/python"

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    cp "$HERE/.env.example" "$ENV_FILE"
    echo "Created $ENV_FILE — edit it (at least MT_MY_NAME_TOKENS and MT_ZOOM_BASE)."
  else
    echo ".env already exists — left as-is."
  fi
}

install_deps() {
  echo "State dir: $STATE"
  mkdir -p "$STATE"
  if [ ! -x "$PY" ]; then
    echo "Creating venv at $VENV ..."
    python3 -m venv "$VENV"
  fi
  echo "Installing Playwright into the venv ..."
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet playwright
  echo "Installing Chromium for Playwright ..."
  "$PY" -m playwright install chromium
  echo "Dependencies ready."
}

do_login() {
  [ -x "$PY" ] || { echo "No venv yet — run ./setup.sh first."; exit 1; }
  echo "Opening Microsoft 365 sign-in (Outlook + Teams share this session) ..."
  "$PY" "$HERE/scripts/o365_login.py" || echo "  O365 login not confirmed — re-run: ./setup.sh --login"
  echo "Opening Zoom sign-in ..."
  "$PY" "$HERE/scripts/zoom_web_login.py" || echo "  Zoom login not confirmed — re-run: ./setup.sh --login"
}

# Sign in to a single service. Used by the guided /morning-triage:setup skill so it can
# open one browser window at a time. $1 = login script name under scripts/.
login_one() {
  [ -x "$PY" ] || { echo "No venv yet — run ./setup.sh first."; exit 1; }
  "$PY" "$HERE/scripts/$1"
}

case "${1:-}" in
  --login) do_login ;;
  --login-o365) login_one o365_login.py ;;
  --login-zoom) login_one zoom_web_login.py ;;
  --check) exec "$PY" "$HERE/scripts/selftest.py" ;;
  "")
    install_deps
    ensure_env
    echo
    echo "Next:"
    echo "  1) Edit $ENV_FILE  (MT_MY_NAME_TOKENS, MT_ZOOM_BASE)"
    echo "  2) ./setup.sh --login   # one-time browser sign-in (a window opens)"
    echo "  3) ./setup.sh --check   # verify everything is ready"
    ;;
  *) echo "Usage: ./setup.sh [--login|--check]"; exit 2 ;;
esac
