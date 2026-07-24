# Changelog

This project follows [Semantic Versioning](https://semver.org/). While at `0.x`, breaking
changes ship in a MINOR bump. Bump the version in **both** `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` whenever you want users to pull an update.

## 0.3.1

### Added
- `setup.sh --check` now reports `plugin_version`, read straight out of the installed code. A plugin host can keep displaying the version it recorded at **install** time even after the code has been updated (observed in the field: the UI said 0.1.0 while the installed code already had the 0.2.0 `/setup` skill), so there was no reliable way to tell what was actually running. README and the skill now point at this field rather than the UI.

## 0.3.0

### Fixed
- **Bare `Teams TIMEOUT`, no data at all.** The three timeouts did not nest: a Bash call caps out near **600s**, `pull_inbox.py` killed the Teams collector at **240s**, and the collector's own chat phase was budgeted for **1200s**. Teams was being killed mid-run every time, and raising the app-load budget in 0.2.4 only squeezed it further. Budgets now nest — chat phase **380s** < subprocess **540s** < caller ~600s — and `pull_inbox.py --only outlook|teams` lets the two collectors run as **separate** calls, each comfortably inside the ceiling. Measured on a warm profile: Outlook 25s; Teams 133s for 35 conversations, `budget_hit=false`.
- A Teams failure can no longer be mistaken for "no messages": the skill must report `app_ready:false` / errors / an empty rail (and the `debug.screenshot` path) explicitly in the digest.

### Added
- **First run offers to schedule itself.** After the first digest, the skill asks once whether to run automatically on weekday mornings, defaulting to **10:00 in the user's local timezone**, and creates the recurring task if accepted.

## 0.2.4

### Fixed
- **"Signed in, but the scraper couldn't open the Chat/Teams tabs — `chats_seen: 0`", persistently, on freshly set-up machines.** Root cause: **signing into Outlook does not initialize Teams.** The first-ever visit to `teams.cloud.microsoft` performs a token exchange (the Skype/ASM tokens) plus a deployment-ring assignment, and the app bar does not exist until that finishes — reproduced at ~40s on a fast connection, longer on a slow one. Every early unattended run was paying that first-visit cost on a budget meant for a warm profile. Setup now **warms Teams once, while the user is present** (`o365_login.py`, up to `MT_TEAMS_WARM_S`, default 240s) so the daily headless pulls start warm; the collector's own budget also went 90s → 180s (`MT_TEAMS_LOAD_TIMEOUT_S`).
- **Opening the Chat tab no longer depends on the UI language.** The app bar's aria-labels are localized — "Chat (⌃⇧2)" becomes "聊天…" on a Chinese UI — so matching the English word was a single point of failure. It now tries the locale-independent `#/chat` route and the Chat keyboard shortcut first, falling back to localized labels. (Verified along the way that `[data-tid='app-bar-chat']`, one of the three original selectors, does not exist in current Teams at all: `count=0`.)
- **An empty chat rail now explains itself.** The collector captures a screenshot (path reported in the JSON), any modal/onboarding overlay text, and the per-selector app-bar state — counters alone cannot distinguish "this account has no chats" from "an overlay is covering the app bar", and only the second is a bug.

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
