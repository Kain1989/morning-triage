#!/usr/bin/env python3
"""Print the signed-in M365 user's display name + derived name tokens, read from the OWA
page title ("Mail - <Display Name> - Outlook"). Reuses $MT_STATE_DIR/o365_profile.
Prints JSON {display_name, tokens} on success, or {"error": ...}. Used by the guided
setup skill to auto-fill MT_MY_NAME_TOKENS so the user is never asked for their name."""
import json
import os
import re
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "o365_profile")


def tokens_from(name):
    """Split a display name into match tokens, e.g. "Doe, Jane (JD)" -> ["doe", "jane", "jd"]
    (deduped, lowercased, tokens of >=2 letters)."""
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", name):
        wl = w.lower()
        if len(wl) >= 2 and wl not in seen:
            seen.add(wl)
            out.append(wl)
    return out


def main():
    out = {"error": "PROBE_FAILED"}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=True, viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(12000)
            if any(k in page.url.lower() for k in ["login", "signin", "/auth"]):
                out = {"error": "NOT_SIGNED_IN"}
            else:
                title = page.title() or ""
                m = re.search(r"Mail\s*-\s*(.+?)\s*-\s*Outlook", title)
                name = (m.group(1).strip() if m else "")
                out = ({"display_name": name, "tokens": tokens_from(name)} if name
                       else {"error": "NAME_NOT_FOUND", "title": title[:120]})
        except Exception as e:
            out = {"error": "PROBE_FAILED", "detail": str(e)[:150]}
        ctx.close()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
