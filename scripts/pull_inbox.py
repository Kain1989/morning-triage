#!/usr/bin/env python3
"""Refresh $MT_STATE_DIR/inbox/ from the headless collectors (Teams + Outlook) for the morning triage intake.

Graceful by design: on auth failure / timeout / missing deps it writes an error JSON and STILL exits 0 —
it must NEVER block the triage run. The morning-triage skill runs this, then reads $MT_STATE_DIR/inbox/.

Outputs (one file per source per day):
  $MT_STATE_DIR/inbox/outlook_<YYYY-MM-DD>.json   {signed_in, mail:[...], calendar:[...]}  (or {"error": ...})
  $MT_STATE_DIR/inbox/teams_<YYYY-MM-DD>.json     {signed_in, activity:[...], chats:[...]}  (or {"error": ...})

If a file holds {"error": "NOT_SIGNED_IN"}, re-run scripts/o365_login.py once (one-time SSO);
until then the skill falls back to the Zoom transcripts + your own notes.
"""
import argparse
import json
import os
import subprocess
import datetime

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
INBOX = os.path.join(STATE, "inbox")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))  # the collectors live next to this file
# The collectors need Playwright, which lives in the plugin venv ($MT_STATE_DIR/venv, created by
# setup.sh) — NOT the system python3. Use it when present; fall back to python3 (the pull then
# degrades gracefully if absent).
_VENV_PY = os.path.join(STATE, "venv", "bin", "python")
PY = _VENV_PY if os.path.exists(_VENV_PY) else "python3"
os.makedirs(INBOX, exist_ok=True)
DATE = datetime.date.today().isoformat()


def pull(script, name, timeout=150):
    out = os.path.join(INBOX, f"{name}_{DATE}.json")
    try:
        r = subprocess.run(
            [PY, os.path.join(SCRIPTS, script)],
            capture_output=True, text=True, timeout=timeout,
        )
        data = json.loads(r.stdout) if r.stdout.strip() else {"error": "NO_OUTPUT", "stderr": (r.stderr or "")[-400:]}
    except subprocess.TimeoutExpired:
        data = {"error": "TIMEOUT", "script": script}
    except Exception as e:  # missing playwright, bad json, etc. — never raise
        data = {"error": "PULL_FAILED", "script": script, "detail": str(e)[:300]}
    with open(out, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if data.get("error"):
        extra = f" — re-run {os.path.join(SCRIPTS, 'o365_login.py')}" if data.get("error") == "NOT_SIGNED_IN" else ""
        return f"{name}: {data['error']}{extra}"
    return (
        f"{name}: signed_in={data.get('signed_in')} "
        f"mail={len(data.get('mail', []))} cal={len(data.get('calendar', []))} "
        f"activity={len(data.get('activity', []))} chats={len(data.get('chats', []))} "
        f"channels={len(data.get('channels', []))}"
    )


def main():
    ap = argparse.ArgumentParser(description="Refresh the inbox from the headless collectors.")
    ap.add_argument("--only", choices=["outlook", "teams"],
                    help="run just one collector (recommended: the caller's own timeout is "
                         "usually ~600s, and Teams alone can need most of that)")
    args = ap.parse_args()

    # Budgets must nest: caller timeout > this subprocess timeout > the collector's internal
    # budget. They previously did not — Teams was killed at 240s while its own chat phase was
    # budgeted for 1200s, which surfaced as a bare "Teams TIMEOUT" with no data. Teams is
    # therefore meant to be invoked as its own call (--only teams).
    jobs = []
    if args.only in (None, "outlook"):
        jobs.append(("outlook_pull.py", "outlook", float(os.environ.get("MT_OUTLOOK_PULL_TIMEOUT_S", "150"))))
    if args.only in (None, "teams"):
        jobs.append(("teams_pull.py", "teams", float(os.environ.get("MT_TEAMS_PULL_TIMEOUT_S", "540"))))
    lines = [pull(script, name, timeout=t) for script, name, t in jobs]
    print(json.dumps({"date": DATE, "inbox": INBOX, "status": lines}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
