# Changelog

## 0.1.0

Initial public release.

- Headless collectors for Microsoft Teams (chats + activity + channels), Outlook (mail + calendar), and Zoom cloud-recording transcripts, all via saved Playwright browser sessions — no computer-use.
- `morning-triage` skill: closure-rule triage over an incremental per-conversation watermark, then a morning digest with paste-ready drafts. Never sends messages.
- `setup.sh` for a one-command venv + Playwright install and guided one-time logins; `selftest.py` preflight with actionable exit codes.
- Fully configurable via `.env` (`MT_*`), with optional Jira and Slack integrations off by default.
