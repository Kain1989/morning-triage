---
name: setup
description: One-time guided install for morning-triage — installs dependencies, collects your config, opens a browser to sign in to Microsoft 365 and Zoom, then self-checks. The user just follows the prompts; use this when someone wants to set up / configure / install morning-triage for the first time.
---

Walk the user through one-time setup, interactively and in order. Do the mechanical steps yourself; pause only when the user must act (answer a question, or sign in via a browser window that opens). Keep each message short and tell the user what's happening. `ROOT` below = `$CLAUDE_PLUGIN_ROOT`.

## STEP 1 — Install dependencies
Run `bash "$ROOT/setup.sh"`. It creates the Playwright venv, installs Chromium, and scaffolds `.env`. If it fails (e.g. no `python3`), tell the user the exact fix and stop here.

## STEP 2 — Collect config, then write it into `.env`
Ask the user these (wait for real answers — never guess or invent them):
1. **Your name as it shows in Teams/Zoom** — a few fragments is fine, e.g. `Jane Doe, jdoe`. This is how triage tells which messages are yours.
2. **Your organization's Zoom portal URL** — e.g. `https://acme.zoom.us`.

(Only if the user raises them: digest language — default English; and whether they use Jira/Slack, which are optional and off by default.)

Then edit `$ROOT/.env` (it was scaffolded from `.env.example` in STEP 1):
- Set `MT_MY_NAME_TOKENS` to the name fragments joined by `;` — e.g. `MT_MY_NAME_TOKENS="jane doe;jdoe"`.
- Set `MT_ZOOM_BASE` to their Zoom URL.
- Leave every other line at its default unless the user asked to change it.

## STEP 3 — Sign in (one service at a time; a browser window opens)
Two independent logins. Do them one at a time so the user only faces one window. **Each waits for the user to finish signing in, so run it with a long timeout (up to the Bash tool's max, ~600s).**

1. Say: "A browser window will open — finish the Microsoft 365 sign-in there (this covers both Outlook and Teams). I'll wait." Then run `bash "$ROOT/setup.sh" --login-o365`. It prints `LOGIN_OK` on success, or `LOGIN_TIMEOUT_OR_FAILED`. If it failed/timed out, offer to retry once.
2. Say: "Now a Zoom sign-in window will open — finish it there." Then run `bash "$ROOT/setup.sh" --login-zoom`. Same handling.

## STEP 4 — Verify and finish
Run `bash "$ROOT/setup.sh" --check` and read the JSON.
- `"ready": true` → tell the user setup is done: they can run `/morning-triage` now, and optionally schedule it to run each weekday morning.
- Not ready → explain the failing check and its fix: exit code `4`/`3` = dependencies (re-run STEP 1); code `2` = a login didn't take (re-run STEP 3 for whichever profile is missing — `o365_profile` or `zoom_profile`).
- Surface any `warnings` (e.g. `.env` still holds placeholder values) and offer to fix them.
