#!/usr/bin/env python3
"""Turn on auto-update for this plugin's marketplace.

Third-party marketplaces ship with auto-update **off** by default. A marketplace that is never
refreshed keeps serving whatever commit it was first cloned at — so a user can reinstall the
plugin over and over and still run months-old code, which is the single most common reason a
shipped fix "doesn't work" for someone. With auto-update on, Claude Code refreshes the
marketplace in the background shortly after a session starts.

This is the scripted equivalent of: /plugin -> Marketplaces -> <name> -> Enable auto-update.

It edits ~/.claude/settings.json (backing it up first) and touches nothing but the
extraKnownMarketplaces entry for this marketplace.

Usage: enable_autoupdate.py [--check] [--marketplace NAME]
Prints JSON.
"""
import argparse
import json
import os
import shutil

SETTINGS = os.path.expanduser("~/.claude/settings.json")
DEFAULT_MARKETPLACE = "morning-triage-marketplace"
DEFAULT_SOURCE = {"source": "github", "repo": "Kain1989/morning-triage"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marketplace", default=DEFAULT_MARKETPLACE)
    ap.add_argument("--check", action="store_true", help="report current state, change nothing")
    args = ap.parse_args()

    data = {}
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS) as f:
                data = json.load(f)
        except Exception as e:
            print(json.dumps({"error": f"could not parse {SETTINGS}: {str(e)[:140]}"}, indent=2))
            return

    markets = data.get("extraKnownMarketplaces") or {}
    entry = dict(markets.get(args.marketplace) or {})
    enabled = bool(entry.get("autoUpdate"))

    if args.check:
        print(json.dumps({"settings": SETTINGS, "marketplace": args.marketplace,
                          "known": args.marketplace in markets, "autoUpdate": enabled}, indent=2))
        return

    if enabled:
        print(json.dumps({"changed": False, "autoUpdate": True,
                          "note": "already enabled"}, indent=2))
        return

    entry.setdefault("source", DEFAULT_SOURCE)
    entry["autoUpdate"] = True
    markets[args.marketplace] = entry
    data["extraKnownMarketplaces"] = markets

    backup = None
    if os.path.exists(SETTINGS):
        backup = SETTINGS + ".bak"
        shutil.copy2(SETTINGS, backup)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print(json.dumps({"changed": True, "autoUpdate": True, "marketplace": args.marketplace,
                      "settings": SETTINGS, "backup": backup,
                      "note": "takes effect on the next Claude Code session"}, indent=2))


if __name__ == "__main__":
    main()
