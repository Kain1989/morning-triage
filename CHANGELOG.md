# Changelog

This project follows [Semantic Versioning](https://semver.org/). While at `0.x`, breaking
changes ship in a MINOR bump. Bump the version in **both** `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` whenever you want users to pull an update.

## 0.2.3

### Fixed
- **A brand-new profile reported `LOGIN_OK` within seconds — before anyone could sign in — and closed the window.** Navigating to `outlook.office.com/mail` renders the title "Outlook" *before* the redirect to the sign-in page, and the UI-based check matched on that title alone. A false success is worse than a hang: setup marches on and every later pull fails as `NOT_SIGNED_IN`. Sign-in is now decided **only** by the SSO cookie **plus** "no tab is still on a sign-in page"; the UI checks are kept purely as diagnostics.

  Found by running the login against an **empty profile** — the one thing that actually reproduces a new user, since an established profile has long since passed every first-run screen. Verified end to end on that empty profile: it waited through the whole sign-in, correctly kept waiting during the window where the cookie already exists but the user is still on the login page, reported `LOGIN_OK` only once the flow completed, and `whoami` then read the identity straight out of the fresh profile.

## 0.2.2

### Fixed
- **Sign-in still appeared to hang after the user had actually signed in** — reported from two different machines: the credentials were entered in the correct window, the page did reach Outlook / the Zoom recordings page, and the script just kept waiting. Root cause: detection inferred "signed in" from the **UI**, and a first-ever login does not render the same screens an established profile does (welcome / onboarding / consent pages match none of the title or body checks).

  Detection now leads with the **authentication cookie** — `ESTSAUTHPERSISTENT` / `ESTSAUTH` / `authtoken` for Microsoft, `_zm_login_acctype` / `_zm_multi_ac` / `_zm_kms` for Zoom — which does not depend on what is rendered; it only additionally requires that no tab is still sitting on a sign-in page. On top of that it checks **every tab** (SSO can finish in one the script never opened), probes the session in a throwaway tab every 15s, prints rich progress every 15s (`auth_cookie=`, `on_signin_page=`, and each tab's URL + title), and dumps every tab's state on timeout — so if it ever stalls again, the log alone says why. The prompt also states explicitly to sign in **in the window that just opened**, since the plugin uses its own browser profile.

## 0.2.1

### Fixed
- **"Signed in, but the Chat/Teams tab wouldn't open — no chats triaged."** Teams web is a heavy SPA, and the collector slept a fixed 16s after `domcontentloaded` before looking for the app bar. That is enough on a warm profile, but a **freshly signed-in profile downloads the entire app bundle first** — so the Chat button wasn't visible yet, `click_first` gave up after 2.5s, and the run returned zero chats while otherwise looking healthy. The collector now waits for the app bar to actually render (`MT_TEAMS_LOAD_TIMEOUT_S`, default 90s), reloads once and retries if it doesn't, reports `app_ready` in its JSON, and warns explicitly when the app never finished loading. Click targets now honour `MT_TEAMS_CLICK_TIMEOUT_MS` (default 5s) instead of a hard-coded 2.5s.

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
