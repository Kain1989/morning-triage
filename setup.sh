#!/usr/bin/env bash
# One-time setup for the morning-triage plugin: create a Playwright venv, scaffold .env,
# and guide the three one-time browser logins. Re-runnable and idempotent.
#
# Usage:
#   ./setup.sh            install deps + create .env   (then edit .env)
#   ./setup.sh --login    open the browsers for the one-time O365 + Zoom sign-in
#   ./setup.sh --whoami   print the signed-in M365 display name + name tokens (JSON)
#   ./setup.sh --check    run the preflight selftest
#   ./setup.sh --paths    print resolved state/inbox/transcripts/log paths (JSON)
#   ./setup.sh --run <script.py> [args]   run a collector with the venv + .env already loaded
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
  --whoami) exec "$PY" "$HERE/scripts/whoami.py" ;;
  # Uses only the standard library, so it works before the venv exists.
  --enable-autoupdate)
    shift
    exec "$([ -x "$PY" ] && echo "$PY" || echo python3)" "$HERE/scripts/enable_autoupdate.py" "$@" ;;
  --check) exec "$PY" "$HERE/scripts/selftest.py" ;;
  # Run any collector with the right venv + .env already loaded, so callers (the skills)
  # never need MT_STATE_DIR or the venv path themselves:
  #   ./setup.sh --run pull_inbox.py
  #   ./setup.sh --run zoom_web_pull.py --limit 5
  --run)
    shift
    [ -n "${1:-}" ] || { echo "usage: ./setup.sh --run <script.py> [args...]"; exit 2; }
    [ -x "$PY" ] || { echo "No venv yet — run ./setup.sh first."; exit 1; }
    script="$1"; shift
    exec "$PY" "$HERE/scripts/$script" "$@" ;;
  # Print the resolved paths as JSON so a caller knows where data and digests land.
  --paths)
    printf '{"state":"%s","inbox":"%s","zoom_transcripts":"%s","log":"%s","plugin":"%s"}\n' \
      "$STATE" "$STATE/inbox" "$STATE/zoom_transcripts" \
      "${MT_LOG_DIR:-$HOME/morning-triage-logs}" "$HERE" ;;
  "")
    install_deps
    ensure_env
    echo
    echo "Next:"
    echo "  1) Edit $ENV_FILE  (MT_MY_NAME_TOKENS, MT_ZOOM_BASE)"
    echo "  2) ./setup.sh --login   # one-time browser sign-in (a window opens)"
    echo "  3) ./setup.sh --check   # verify everything is ready"
    ;;
  *) echo "Usage: ./setup.sh [--login|--login-o365|--login-zoom|--whoami|--check|--paths|--enable-autoupdate|--run <script.py> [args]]"; exit 2 ;;
esac
