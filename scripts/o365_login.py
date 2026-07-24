#!/usr/bin/env python3
"""One-time Microsoft 365 web sign-in (shared profile for Outlook + Teams web).
Opens a real browser window with a persistent profile under $MT_STATE_DIR/o365_profile;
you complete your organization's Microsoft 365 SSO once. The same Microsoft session cookie
then covers outlook.office.com AND teams.microsoft.com. Exits LOGIN_OK when OWA mail is reachable."""
import os, sys, time
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "o365_profile")
URL = "https://outlook.office.com/mail/"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=False, viewport={"width": 1280, "height": 880}
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("BROWSER_OPEN: complete your Microsoft 365 SSO sign-in in the window", flush=True)
    deadline = time.time() + 600
    ok = False
    while time.time() < deadline:
        time.sleep(3)
        try:
            u = page.url
        except Exception:
            print("WINDOW_CLOSED", flush=True)
            break
        # Signed-in OWA lands on outlook.office.com/mail without a login/auth bounce
        if "outlook.office.com" in u and "/mail" in u and "login" not in u and "auth" not in u:
            try:
                # the mail app shows a "New mail"/"New message" button or a message list
                body = page.inner_text("body").lower()
                if "inbox" in body or "new mail" in body or "new message" in body or "收件箱" in body:
                    ok = True
                    break
            except Exception:
                pass
    print("LOGIN_OK" if ok else "LOGIN_TIMEOUT_OR_FAILED", flush=True)
    ctx.close()
    sys.exit(0 if ok else 1)
