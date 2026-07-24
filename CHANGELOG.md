# Changelog

## 0.1.0

Initial public release.

- Headless collectors for Microsoft Teams (chats + activity + channels), Outlook (mail + calendar), and Zoom — all via saved Playwright browser sessions, no computer-use. The Zoom collector covers both **My Recordings** and **Shared with me**, pulling transcripts (`.vtt`) and AI Companion summaries (overview + next steps + chapters, via the `web_view_summary` API).
- `morning-triage` skill: closure-rule triage over an incremental per-conversation watermark, then a morning digest with paste-ready drafts. Never sends messages.
- `setup.sh` for a one-command venv + Playwright install and guided one-time logins; `selftest.py` preflight with actionable exit codes.
- A guided `/morning-triage:setup` skill — **two sign-ins and nothing to type**: installs deps, opens the Microsoft 365 and Zoom sign-ins one at a time, auto-detects your display name (`whoami.py`, read from the signed-in M365 profile) to fill `MT_MY_NAME_TOKENS`, then self-checks. Zoom needs no org URL — the generic `zoom.us` host serves both recordings and summaries.
- Fully configurable via `.env` (`MT_*`), with optional Jira and Slack integrations off by default.
- Inbox hygiene, **on by default**: `mark_mail.py` marks the mail the digest covered as **read**, and keeps — or sets back — only the items that need your action as **unread**. You read the digest, so nothing it already handled should still be competing for your attention. Uses the Unread filter + right-click menu (the only combination that works headless; the menu is state-dependent) and always restores the filter. `MT_MARK_MAIL_READ=0` disables it, `--dry-run` previews. Teams needs no equivalent: opening a conversation already marks it read, and Teams mark-as-unread is not solvable headless.
- **One lookback window for every source**: `MT_LOOKBACK_DAYS` (default 3) now governs Teams' first look at a conversation, how much of the inbox is read, and how old a Zoom meeting may be. Teams still resumes from each conversation's own watermark after that first look.
- Skills no longer depend on shell variables that aren't there. `${CLAUDE_PLUGIN_ROOT}` is not set inside the Bash tool's shell (and each Bash call is a fresh shell), so the very first setup step failed with `bash: /setup.sh: No such file or directory` (exit 127). Both skills now spell out the absolute-path rule, and every collector call goes through `setup.sh --run` / `--paths`, which load `.env` and the venv themselves — so a skill never needs `MT_STATE_DIR` or a python path.
- Sign-in detection is host-agnostic. Microsoft moved OWA from `outlook.office.com` to `outlook.cloud.microsoft`, and hard-coding the old host made a *successful* sign-in look like a failure and hang until timeout. Both logins now detect "not on a sign-in page + the app actually rendered", print progress every 30s, and report the last URL/title on timeout.
