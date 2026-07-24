# Security & Privacy

## What leaves your machine

Nothing, by default. The plugin reads your Teams/Outlook/Zoom through headless browser sessions and writes results to local files. It does not upload your data anywhere. The only optional outbound actions are:

- **Jira** issue creation (only if `MT_JIRA_ENABLED=1`), and
- a **Slack** digest summary (only if `MT_SLACK_ENABLED=1`),

both to destinations you configure. The plugin **never** sends Teams/Outlook messages, posts to repositories, or pushes git.

## Where sensitive data lives

Everything sensitive is under `MT_STATE_DIR` (default `~/.morning-triage`), which is **outside this repository**:

- `o365_profile/`, `zoom_profile/` — persistent browser sessions (contain auth cookies). Treat these like credentials; anyone with them can act as you in those web apps.
- `inbox/*.json`, `zoom_transcripts/` — collected messages and transcripts.
- `chat_watermark.json` — per-conversation "last seen" timestamps.

`.gitignore` also excludes these names in case you point `MT_STATE_DIR` inside the tree. `.env` is gitignored.

## Secrets

This repo contains **no secrets**. There are no API keys or passwords in the code — authentication is entirely via the interactive browser sign-in you do once in `setup.sh --login`. Do not add tokens to tracked files; put configuration in `.env`.

## Sessions

The Microsoft 365 (Outlook + Teams share one) and Zoom sessions are stored locally and expire after weeks. When a collector reports `NOT_SIGNED_IN`, re-run `./setup.sh --login`. Revoking access is as simple as deleting the profile directory or signing out in your org's identity provider.

## Reporting

Found a problem? Open an issue (omit any real data or cookies from the report).
