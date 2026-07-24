---
name: morning-triage
description: Collect Teams chats, Outlook mail/calendar, and Zoom transcripts, AI summaries and My Notes (all headless, via saved browser sessions), triage what actually needs your reply using closure rules, and write a morning digest with paste-ready drafts. Never sends messages.
---

Run the morning triage routine autonomously — do not pause to ask questions. The deliverable is a **morning digest**: what needs your reply (with drafts), today's meetings, action items, and carry-over from yesterday. **Never send any message** — every draft is for the user's approval only.

## Configuration

All settings come from the environment (load `.env` at the plugin root if present). None are secret; secrets never live in this repo.

| Var | Default | Purpose |
|---|---|---|
| `MT_STATE_DIR` | `~/.morning-triage` | Browser profiles, watermark, collected inbox, transcripts. |
| `MT_LOG_DIR` | `~/morning-triage-logs` | Where the digest markdown is written. |
| `MT_MY_NAME_TOKENS` | *(empty)* | `;`-separated fragments of your own name/handle — used to detect messages you sent (closure). |
| `MT_ZOOM_BASE` | `https://zoom.us` | Your org Zoom portal, e.g. `https://yourorg.zoom.us`. |
| `MT_DIGEST_LANG` | `English` | Language to write the digest in. |
| `MT_WORKSPACE_DIR` | *(unset)* | Optional codebase to verify factual answers against before drafting. |
| `MT_JIRA_ENABLED` / `MT_JIRA_PROJECT` / `MT_JIRA_ASSIGNEE_ACCOUNT_ID` | `0` | Optional: file Jira issues for new actionable work. |
| `MT_SLACK_ENABLED` / `MT_SLACK_CHANNEL_ID` | `0` | Optional: post the digest summary to a Slack channel. |
| `MT_LOOKBACK_DAYS` | `3` | How far back **every** source looks: Teams' first look at a conversation (afterwards it resumes from that conversation's watermark), how much of the inbox is read, and how old a Zoom meeting may be. |
| `MT_MARK_MAIL_READ` | `1` | Mark digested mail read and leave only action items unread. Set `0` to never touch mail state. |

## Running commands

Every command goes through `setup.sh`, which loads `.env` and the correct venv for you — so you never need `MT_STATE_DIR` or a python path yourself.

`ROOT` = the plugin's **absolute** path. Claude Code exposes it as `${CLAUDE_PLUGIN_ROOT}`, but **that variable is not set inside the Bash tool's shell, and every Bash call is a fresh shell** — a bare `"$CLAUDE_PLUGIN_ROOT/..."` expands to `/...` and fails with `exit code 127`. This file is at `<ROOT>/skills/morning-triage/SKILL.md`, so inline the absolute path (or re-export it in that same call) every time.

Start with `bash "$ROOT/setup.sh" --paths`: it prints `{state, inbox, zoom_transcripts, log, plugin}` — use those paths when reading collected data and writing the digest.

## Ground rules

- **All collection is HEADLESS Playwright over saved browser sessions. NEVER use computer-use / GUI automation.**
- The three sessions expire independently (weeks-long). If any source reports `NOT_SIGNED_IN` or errors, note it in the digest and continue with the other sources — never stop the run. A re-login needs the user present once; flag it.
- **Never** send Teams/Outlook messages, post to repos, or push git. Those are gated on the user's explicit approval.

## STEP 0 — Preflight

Run `bash "$ROOT/setup.sh" --check` and read its JSON. Its `plugin_version` field is the version of the code **actually installed** — a plugin host may keep displaying the version recorded at install time, so trust this field, not the UI, when the user asks what they're running. Exit code `4`/`3` → tell the user to run `setup.sh`. Exit code `2` → note which login is missing (`o365_login.py` and/or `zoom_web_login.py`) and continue with whatever IS signed in. Surface any `warnings` (e.g. `MT_MY_NAME_TOKENS` unset).

## STEP 1 — Teams + Outlook (headless O365)

Run these as **two separate Bash calls**. Teams alone can need most of a Bash call's ~600s ceiling; putting both in one call is what produced bare `Teams TIMEOUT` runs with no data at all:

```bash
bash "$ROOT/setup.sh" --run pull_inbox.py --only outlook    # quick, ~1 min
bash "$ROOT/setup.sh" --run pull_inbox.py --only teams      # slow — give this call your maximum timeout (~600s)
```

Both write into the `inbox` directory reported by `--paths` (graceful: an error is written as JSON, it never blocks). Read both files.

**Never let a Teams failure look like "no messages".** If the Teams JSON has `app_ready: false`, an `error`, or an empty `chat_threads` carrying a `debug` block, say so explicitly in the digest — and surface `debug.screenshot` if present so the user can look at what the page actually showed. Group-chat needs-reply items are a blind spot in that case, and the user must know.

**Teams** JSON: `{signed_in, activity, chats, chat_threads, channels, ...coverage}`.
- **`chat_threads` is the PRIMARY signal for "who needs a reply", NOT `activity`.** Each thread is a conversation with activity since its watermark, carrying `messages:[{author, ts, text, is_me}]` plus `last_sender`, `last_is_me`, `msg_count`, `new_count` (messages strictly after the watermark), `oldest_ts`, `newest_ts`. Focus on threads with `new_count > 0`.
- **`activity` is NOTIFICATION HISTORY, not a to-do queue.** A "mentioned you" entry stays there forever and does not include your own replies — it can only *nominate* a candidate conversation, never conclude that you still owe a reply. Cross-check every candidate against its `chat_threads` entry.

**Outlook** JSON: `{signed_in, mail, calendar}`. `mail` = today's inbox rows (unread flag + sender + subject + preview); focus on work-relevant unread, skip promos/newsletters. `calendar` = today's meetings with time/organizer.

## STEP 2 — Zoom recordings, transcripts, AI summaries & My Notes

Run `bash "$ROOT/setup.sh" --run zoom_web_pull.py --limit 5` (it writes to the `zoom_transcripts` path from `--paths` by default). It covers BOTH **My Recordings** and **Shared with me**, and pulls:
- **Transcripts** — `<out>/<date>_<topic>.vtt` per recording (my + shared).
- **AI Companion summaries** — `<out>/summaries/<date>_<topic>.summary.md` (overview + next steps + chapters) plus the raw `.summary.json`.
- **My Notes** — `<out>/notes/<date>_<title>.note.md`: the AI-structured meeting notes (key outcomes, decisions made, open questions, action items). Skip with `--no-notes`.

It writes `<out>/index.json` = `{recordings:[{source, topic, has_transcript, files}], summaries:[{scope, topic, overview, next_steps, chapters, file}], notes:[{id, title, updated, file, text}]}`, where `source`/`scope` is `my` | `shared`.

For anything NEW since the last run (compare against the prior `index.json`), extract decisions, action items (owner + ask) and open questions. **Use the richest source per meeting, in this order: My Note → AI summary → transcript** — a note or summary already carries the decisions and action items, so only fall back to the raw transcript when neither exists. If it prints `NOT_SIGNED_IN`, skip Zoom this run and note the Zoom session needs re-login (`zoom_web_login.py`).

## STEP 3 — Triage (closure rules — this is the whole point)

Classify everything into **needs-reply** (a direct or open question to you, noting how long it has waited) / **task** (actionable work) / **FYI**.

Apply these closure rules before listing ANY needs-reply item — do not skip them:
1. `last_is_me == true` → you already replied → **CLOSED**, drop it.
2. Last message is someone else giving a substantive answer to the open question (answered by anyone, not necessarily you) → **CLOSED**, drop it.
3. Last message is a question/request from someone else with NO answer after it → **needs-reply**. This holds whether or not you were @-mentioned — an unanswered general question in a group is still a legitimate task for you.
4. A reaction / "done" / ✅ acknowledging the message → treat as **CLOSED**.

**Watermark model:** `chat_watermark.json` (in the `state` directory from `--paths`) stores, per conversation, the newest message ts seen. Each run reads only messages after that ts, then advances it — so nothing is missed and nothing is re-triaged. First run / a brand-new conversation looks back to `TEAMS_CHAT_SINCE` (ISO or local wall-clock; default = yesterday noon). To re-scan a window, delete the watermark or set `TEAMS_CHAT_SINCE`. (Known trap: if some other process advanced the watermark, a thread can show `0 new` while real messages exist — if a thread you expect activity in is silent, back up the watermark and force a re-scan with `TEAMS_CHAT_SINCE`.)

**Coverage honesty:** the Teams JSON reports `chats_seen`, `rail_rows_scanned`, `chats_skipped`, `budget_hit`. The chat rail has a render race (sometimes only ~6 rows appear for a few seconds before the true ~50 populate); the scraper nudges via scroll until it sees ≥ `TEAMS_RAIL_MIN` (default 10) leaves. If `chats_seen` looks implausibly low or `budget_hit` is true, re-run (budget `TEAMS_CHAT_BUDGET_S`, default 1200s). Opening a conversation marks it read.

If `MT_WORKSPACE_DIR` is set and a message asks something answerable from that codebase, verify the answer in source before drafting — never guess table/column/API names.

## STEP 4 — Digest + drafts

Write the digest in `MT_DIGEST_LANG` to `<log>/<YYYY-MM-DD>.md` (the `log` path from `--paths`; append a `# Morning triage — <date>` section if the file exists), containing:
- a **needs-reply table** with paste-ready reply drafts,
- today's calendar with any prep notes,
- meeting takeaways from new Zoom material (My Notes / AI summaries / transcripts), carrying their action items into the needs-reply or task lists when they are yours,
- **carry-over** open items still awaiting the user (read yesterday's digest in `$MT_LOG_DIR`).

Then:
- Draft replies but **DO NOT send them** — they go in the digest for approval.
- If `MT_JIRA_ENABLED=1` and an item is new actionable work not already tracked, you MAY create a Jira issue in `MT_JIRA_PROJECT` (assign `MT_JIRA_ASSIGNEE_ACCOUNT_ID`) — but only after confirming it is NOT closed by the rules above, and check existing issues first to avoid duplicates.
- If `MT_SLACK_ENABLED=1`, post a short (<15 line) summary to channel `MT_SLACK_CHANNEL_ID` via the Slack MCP. This is the one pre-approved outbound write.
- Finish with the digest as your final chat message so the user sees it.

## STEP 5 — Reflect the triage in the inbox (on by default)

The point of this plugin is that the user reads the **digest** instead of the inbox. So what you digested should stop competing for attention, and only what needs their action should stand out.

Unless `MT_MARK_MAIL_READ=0`, run this **after** the digest is written:

```bash
bash "$ROOT/setup.sh" --run mark_mail.py --limit 50 --unread "<fragment>" --unread "<fragment>"
```

Pass one `--unread` for **every item you listed as needs-reply or a task**, using a distinctive sender or subject fragment. Those are kept — or set back to — unread, so they are the only mail still standing out in Outlook. Everything else you digested is marked read. Use `--dry-run` first if the user wants a preview. Report the counts.

Teams needs no equivalent step: opening a conversation already marks it read, and Teams mark-as-unread is not solvable headless — so for Teams, the digest itself is the reminder.

## STEP 6 — First run only: offer to make it a daily routine

If this looks like the user's **first** run (the `log` directory from `--paths` holds no earlier digest), ask once whether they want this to run automatically on weekday mornings. Propose **10:00 in their local timezone** as the default, and let them choose a different time.

If they accept, create the recurring task with whatever scheduling this Claude Code provides (e.g. the `schedule` skill / scheduled tasks), pointing it at `/morning-triage`. Tell them what you created and how to cancel it. If they decline, don't bring it up again on later runs.
