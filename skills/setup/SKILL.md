---
name: setup
description: One-time guided install for morning-triage — installs dependencies, opens a browser to sign in to Microsoft 365 and Zoom, auto-detects your name, and self-checks. Two sign-ins and nothing to type; use this when someone wants to set up / configure / install morning-triage for the first time.
---

Walk the user through one-time setup. It requires **no questions** — just two browser sign-ins; everything else you do yourself. Keep each message short and say what's happening.

## STEP 0 — Resolve the plugin path first (this is the #1 way setup fails)

Every command below needs the plugin's **absolute** path, written `ROOT` here. Claude Code exposes it as `${CLAUDE_PLUGIN_ROOT}`, **but that variable is not set inside the Bash tool's shell, and every Bash call is a fresh shell** — so a bare `bash "$CLAUDE_PLUGIN_ROOT/setup.sh"` expands to `/setup.sh` and dies with `exit code 127`.

You already know the path: this file is at `<ROOT>/skills/setup/SKILL.md`. In **every** Bash call, either inline the absolute path or re-export it *in that same call*:

```bash
bash "/abs/path/to/morning-triage/setup.sh" --check
# or
export CLAUDE_PLUGIN_ROOT="/abs/path/to/morning-triage" && bash "$CLAUDE_PLUGIN_ROOT/setup.sh" --check
```

Confirm once with `ls "<ROOT>/setup.sh"` before STEP 1, and don't proceed until it resolves.

## STEP 1 — Install dependencies
Run `bash "$ROOT/setup.sh"`. It creates the Playwright venv, installs Chromium, and scaffolds `.env`. If it fails (e.g. no `python3`), give the user the exact fix and stop here.

## STEP 2 — Sign in to Microsoft 365 (a browser window opens)
Tell the user: "A browser window will open — finish the Microsoft 365 sign-in there. This one login covers both Outlook and Teams. I'll wait."

Run `bash "$ROOT/setup.sh" --login-o365`. **It waits for the user, so give it a long timeout (up to the Bash tool's max, ~600s).** It prints `LOGIN_OK` or `LOGIN_TIMEOUT_OR_FAILED`; on failure, offer one retry.

## STEP 3 — Sign in to Zoom (a browser window opens)
Tell the user: "Now a Zoom sign-in window will open — sign in as you normally would at zoom.us."

Run `bash "$ROOT/setup.sh" --login-zoom` the same way (long timeout, one retry on failure). Nothing org-specific is needed: the collector works off the generic `zoom.us` host, which serves both recordings and AI summaries.

## STEP 4 — Auto-detect the user's name (do not ask them)
Run `bash "$ROOT/setup.sh" --whoami`. On success it prints `{"display_name": "...", "tokens": ["..."]}` read from the signed-in Microsoft 365 profile.

Write the tokens into `$ROOT/.env` as `MT_MY_NAME_TOKENS="tok1;tok2;tok3"`, then tell the user which name was detected so they can correct it if it looks wrong. This is what lets triage tell your own messages apart from everyone else's.

- `{"error": "NOT_SIGNED_IN"}` → STEP 2 didn't take; retry it, then retry this.
- `{"error": "NAME_NOT_FOUND"}` → only now ask the user for their Teams display name, and write that.

## STEP 5 — Verify and finish
Run `bash "$ROOT/setup.sh" --check` and read the JSON.
- `"ready": true` → tell the user setup is complete: they can run `/morning-triage` now, and optionally schedule it each weekday morning.
- Not ready → explain the failing check and its fix: exit code `4`/`3` = dependencies (re-run STEP 1); code `2` = a login didn't take (re-run STEP 2 or STEP 3 for whichever of `o365_profile` / `zoom_profile` is missing).
- Surface any `warnings` and offer to fix them.
