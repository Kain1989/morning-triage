# Changelog

This project follows [Semantic Versioning](https://semver.org/). While at `0.x`, breaking
changes ship in a MINOR bump. Bump the version in **both** `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` whenever you want users to pull an update.

## 0.2.0

Everything here came out of running the plugin on real machines.

### Added
- **Zoom now covers both "My Recordings" and "Shared with me"**, and pulls transcripts *and* AI Companion summaries (overview + next steps with assignees + chapters), via the `web_view_summary` API.
- **Guided `/morning-triage:setup` skill — two sign-ins and nothing to type.** It installs dependencies, opens the Microsoft 365 and Zoom sign-ins one at a time, auto-detects your display name (`whoami.py`, read from the signed-in M365 session) and self-checks. Zoom needs no org URL: the generic `zoom.us` host serves recordings and summaries.
- **Inbox hygiene, on by default** (`mark_mail.py`): mail the digest covered is marked **read**, and only items that still need your action are left — or set back — to **unread**. You read the digest, so nothing it handled should still compete for attention. `MT_MARK_MAIL_READ=0` disables it; `--dry-run` previews; `--unread` marks action items.
- **One lookback window for every source**: `MT_LOOKBACK_DAYS` (default 3) governs Teams' first look at a conversation, how much of the inbox is read, and how old a Zoom meeting may be. Teams still resumes from each conversation's own watermark afterwards.
- `setup.sh` subcommands: `--login-o365`, `--login-zoom`, `--whoami`, `--paths`, `--run <script> [args]`.

### Fixed
- **Sign-in hung after a *successful* login.** Detection required the legacy `outlook.office.com` host, but OWA now redirects to `outlook.cloud.microsoft`, so a completed sign-in was never recognized. Detection is host-agnostic now, prints progress every 30s and reports the last URL/title on timeout. Zoom had the same class of bug for a different reason — its body fallback lived in an `except` branch, but `is_visible()` returning `False` is not an exception, so the fallback never ran.
- **The first setup step failed with `exit 127`.** `${CLAUDE_PLUGIN_ROOT}` is not set inside the Bash tool's shell (and each call is a fresh shell), so `"$CLAUDE_PLUGIN_ROOT/setup.sh"` expanded to `/setup.sh`. Both skills now state the absolute-path rule, and every collector call goes through `setup.sh`, which loads `.env` and the venv itself.
- **Shared-with-me Zoom summaries came back empty**: that list nests its rows under `result.pageResult.data`, unlike the "My" list (`result.data`).
- **`mark_mail.py` could never restore an already-read message.** The inbox list is virtualized — only ~10 rows exist in the DOM and scrolling does not reliably materialize more — so it now locates those messages via OWA search. Dry runs also state explicitly that the preview is partial: a real run keeps surfacing rows as marked ones leave the Unread view, and will mark more than the preview lists.

### Changed (breaking)
- `MT_FIRST_RUN_DAYS` → `MT_LOOKBACK_DAYS`.
- `mark_mail_read.py` → `mark_mail.py`.
- `MT_MARK_MAIL_READ` now defaults to `1` (was off).

## 0.1.0

Initial public release: headless Teams / Outlook / Zoom collectors over saved Playwright
sessions (no computer-use), the `morning-triage` skill — closure-rule triage over a
per-conversation watermark, producing a digest with paste-ready drafts and never sending
anything — plus `setup.sh`, the `selftest.py` preflight with actionable exit codes, and
`.env`-driven configuration with optional Jira and Slack integrations.
