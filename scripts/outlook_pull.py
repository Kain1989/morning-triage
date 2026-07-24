#!/usr/bin/env python3
"""Headless Outlook (OWA) pull — today's inbox + calendar — via the shared M365 profile.
No computer-use, no cursor takeover, no authorize dialog. Reuses $MT_STATE_DIR/o365_profile
(one-time SSO via o365_login.py). Prints JSON: {signed_in, mail:[...], calendar:[...]}.
If the session expired it prints {"error":"NOT_SIGNED_IN"} — re-run o365_login.py."""
import json, os, sys
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "o365_profile")
# The inbox list is newest-first, so rows are a proxy for the lookback window: read enough of
# them to cover MT_LOOKBACK_DAYS days. Each row keeps its own timestamp text, so triage can
# still tell how old a message is.
LOOKBACK_DAYS = float(os.environ.get("MT_LOOKBACK_DAYS", "3"))
MAX_ROWS = int(os.environ.get("MT_MAIL_ROWS", str(int(max(30, LOOKBACK_DAYS * 25)))))


def signed_out(url):
    u = url.lower()
    return any(k in u for k in ["login.microsoft", "login.live", "/auth", "signin", "adfs", "okta"])


def main():
    out = {"signed_in": False, "mail": [], "calendar": []}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=True, viewport={"width": 1500, "height": 950})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(12000)
        if signed_out(page.url):
            print(json.dumps({"error": "NOT_SIGNED_IN", "hint": "re-run scripts/o365_login.py"}))
            ctx.close()
            return
        out["signed_in"] = True

        rows = page.locator("div[role='option']")
        for i in range(min(rows.count(), MAX_ROWS)):
            try:
                al = rows.nth(i).get_attribute("aria-label") or rows.nth(i).inner_text()
            except Exception:
                continue
            if not al:
                continue
            al = " ".join(al.split())
            out["mail"].append(al[:400])

        for url in ("https://outlook.office.com/calendar/view/day",):
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(11000)
            evs = page.eval_on_selector_all(
                "div[aria-label], [role='button'][aria-label], [role='listitem'][aria-label]",
                "els => els.map(e=>e.getAttribute('aria-label'))"
                ".filter(a=>a && /\\d{1,2}:\\d{2}/.test(a) && (a.includes(' to ')||a.includes('AM')||a.includes('PM')))",
            )
            seen = set()
            for e in evs:
                e = " ".join(e.split())
                key = e[:60]
                if key not in seen and "event" not in e[:15].lower():
                    seen.add(key)
                    out["calendar"].append(e[:300])

        ctx.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
