# Morning Triage

A [Claude Code](https://claude.com/claude-code) plugin that runs your morning comms triage **headlessly**. It collects Microsoft Teams chats, Outlook mail + calendar, and Zoom cloud-recording transcripts from browser sessions you sign into once, decides what actually needs *your* reply (not merely what mentioned you), and writes a morning digest with paste-ready drafts. **It never sends anything** — drafts are for your approval.

## What it does

- **Teams / Outlook / Zoom**, collected via headless Playwright over saved browser sessions. No computer-use, no cursor takeover, no per-run auth dialogs — it runs unattended. Zoom covers both **My Recordings** and **Shared with me**, pulling transcripts *and* AI Companion summaries (overview + next steps + chapters).
- **Closure-rule triage.** A thread only surfaces as *needs-reply* if its last message is an unanswered question to you. Threads you already answered, that someone else answered, or that were acknowledged (👍/✅/"done") are dropped. An incremental **watermark** means each run only looks at messages newer than the last run.
- **A digest** in your language: a needs-reply table with drafts, today's calendar, meeting takeaways from new transcripts, and carry-over items from yesterday. Optional Jira filing and Slack summary.

## Requirements

- macOS or Linux, Python 3.9+
- A browser sign-in to your Microsoft 365 and Zoom accounts
- Claude Code

## Install

```
/plugin marketplace add Kain1989/morning-triage
/plugin install morning-triage@morning-triage-marketplace
```

Or clone this repo and add it as a local marketplace with `/plugin marketplace add ./morning-triage`.

## Setup (one time)

**Easiest — guided, inside Claude Code.** Just run:

```
/morning-triage:setup
```

**Two sign-ins, nothing to type.** It installs dependencies, opens a browser for you to sign in to Microsoft 365 (one login covers Outlook *and* Teams) and then Zoom, auto-detects your display name from the signed-in M365 profile, and verifies everything.

**Or manually**, from the plugin directory:

```bash
./setup.sh            # create the Playwright venv + scaffold .env
./setup.sh --login    # a browser window opens: complete M365 SSO, then Zoom SSO
./setup.sh --whoami   # auto-detect your name → put the tokens in .env as MT_MY_NAME_TOKENS
./setup.sh --check    # preflight — should print "ready": true
```

The two logins are independent and last for weeks; you only redo one when a run reports `NOT_SIGNED_IN` for it.

### Staying up to date

**Turn on auto-update once.** Third-party marketplaces have it **off by default**, which is the single most common reason a shipped fix appears not to work:

```
/plugin  →  Marketplaces  →  morning-triage-marketplace  →  Enable auto-update
```

or let setup do it for you (it backs up `~/.claude/settings.json` first):

```bash
./setup.sh --enable-autoupdate
```

With it on, Claude Code refreshes the marketplace in the background shortly after a session starts, then prompts you to run `/reload-plugins`. Check the current state any time with `./setup.sh --enable-autoupdate --check`.

### Which version am I actually running?

`./setup.sh --check` reports `plugin_version`, read straight out of the installed code. Trust that over any number a plugins UI shows you.

**Without auto-update, reinstalling is not enough.** The marketplace lives on your machine as a **git clone**, and uninstall/install simply reinstalls out of that clone — so if the clone is stale you get the same old version back, no matter how many times you reinstall. Refresh the marketplace first:

```
/plugin marketplace update morning-triage-marketplace
```

then reinstall (or hit **Update**). The manual equivalent, if the UI won't cooperate:

```bash
git -C ~/.claude/plugins/marketplaces/morning-triage-marketplace pull
```

Then confirm with `./setup.sh --check` that `plugin_version` actually moved. A stale clone is silent — it looks like a working install of an old version, which is exactly how several fixes appeared "not to work".

## Run

In Claude Code:

```
/morning-triage
```

It collects all three sources, triages, writes the digest to `MT_LOG_DIR`, and prints it back to you. To automate it, point cron or Claude Code's scheduler at the skill each weekday morning.

## Configure

All settings live in `.env` (see [`.env.example`](.env.example)):

| Var | Purpose |
|---|---|
| `MT_MY_NAME_TOKENS` | Auto-detected during setup from your M365 profile. `;`-separated name fragments — how it tells your own messages apart (closure). |
| `MT_ZOOM_BASE` | Defaults to `https://zoom.us`, which serves recordings *and* summaries for most accounts. Override only if your org requires its vanity host. |
| `MT_STATE_DIR` | Where sessions/watermark/collected data live (default `~/.morning-triage`, outside this repo). |
| `MT_LOG_DIR` | Where the digest markdown is written. |
| `MT_DIGEST_LANG` | Language to write the digest in (default English). |
| `MT_WORKSPACE_DIR` | Optional codebase to verify factual answers against before drafting. |
| `MT_LOOKBACK_DAYS` | How far back **every** source looks (default 3 days): Teams' first look at a conversation (then it resumes from that conversation's watermark), how much of the inbox is read, and how old a Zoom meeting may be. |
| `MT_MARK_MAIL_READ` | On by default: mail the digest covered is marked **read**, and only items needing your action are left **unread**. Set `0` to never touch mail state. |
| `MT_JIRA_*` / `MT_SLACK_*` | Optional integrations (off by default). |

## How it works

```
setup.sh ── venv + Playwright + one-time logins ──►  $MT_STATE_DIR/{o365_profile,zoom_profile}
                                                          │
/morning-triage (skills/morning-triage/SKILL.md)          │ reuses saved sessions
     │                                                    ▼
     ├─ scripts/pull_inbox.py ─► outlook_pull.py + teams_pull.py ─► $MT_STATE_DIR/inbox/*.json
     ├─ scripts/zoom_web_pull.py ─► my + shared: transcripts + AI summaries ─► $MT_STATE_DIR/zoom_transcripts/
     └─ triage (closure rules + watermark) ──────────────────────► $MT_LOG_DIR/<date>.md  (the digest)
```

## Privacy & safety

Collected messages, browser sessions, and the watermark stay under `MT_STATE_DIR` **on your machine — never in this repo** (`.gitignore` enforces it, and `.env` is ignored too). The plugin never sends a message, posts to a repo, or pushes git. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE).
