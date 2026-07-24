# Changelog

## 0.1.0

Initial public release.

- Headless collectors for Microsoft Teams (chats + activity + channels), Outlook (mail + calendar), and Zoom — all via saved Playwright browser sessions, no computer-use. The Zoom collector covers both **My Recordings** and **Shared with me**, pulling transcripts (`.vtt`) and AI Companion summaries (overview + next steps + chapters, via the `web_view_summary` API).
- `morning-triage` skill: closure-rule triage over an incremental per-conversation watermark, then a morning digest with paste-ready drafts. Never sends messages.
- `setup.sh` for a one-command venv + Playwright install and guided one-time logins; `selftest.py` preflight with actionable exit codes.
- A guided `/morning-triage:setup` skill — **two sign-ins and nothing to type**: installs deps, opens the Microsoft 365 and Zoom sign-ins one at a time, auto-detects your display name (`whoami.py`, read from the signed-in M365 profile) to fill `MT_MY_NAME_TOKENS`, then self-checks. Zoom needs no org URL — the generic `zoom.us` host serves both recordings and summaries.
- Fully configurable via `.env` (`MT_*`), with optional Jira and Slack integrations off by default.
- Sign-in detection is host-agnostic. Microsoft moved OWA from `outlook.office.com` to `outlook.cloud.microsoft`, and hard-coding the old host made a *successful* sign-in look like a failure and hang until timeout. Both logins now detect "not on a sign-in page + the app actually rendered", print progress every 30s, and report the last URL/title on timeout.
