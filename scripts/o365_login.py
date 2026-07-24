#!/usr/bin/env python3
"""One-time Microsoft 365 web sign-in (shared profile for Outlook + Teams web).
Opens a real browser window with a persistent profile under $MT_STATE_DIR/o365_profile;
you complete your organization's Microsoft 365 SSO once. The same Microsoft session cookie
then covers Outlook AND Teams. Exits LOGIN_OK once a mailbox is actually reachable.

Detection is HOST-AGNOSTIC on purpose: Microsoft moves OWA between hosts (it served
outlook.office.com, now redirects to outlook.cloud.microsoft), and hard-coding a host made
a successful sign-in look like a failure and hang until timeout. We instead check
"not on a sign-in page" + "a mailbox actually rendered"."""
import os
import sys
import time
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "o365_profile")
URL = "https://outlook.office.com/mail/"
TIMEOUT_S = float(os.environ.get("MT_LOGIN_TIMEOUT_S", "600"))
# Identity-provider / sign-in URL markers: if we're on one of these, we're not signed in yet.
IDP = ("login.microsoft", "login.live", "login.windows", "signin", "adfs", "okta", "/auth")


def signed_in(page):
    u = (page.url or "").lower()
    if any(k in u for k in IDP) or "/mail" not in u:
        return False
    try:
        if "outlook" in (page.title() or "").lower():
            return True
        body = page.inner_text("body").lower()
        return any(k in body for k in ("inbox", "new mail", "new message", "收件箱"))
    except Exception:
        return False


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=False, viewport={"width": 1280, "height": 880}
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("BROWSER_OPEN: complete your Microsoft 365 SSO sign-in in the window", flush=True)

    deadline = time.time() + TIMEOUT_S
    ok, waited, last = False, 0, URL
    while time.time() < deadline:
        time.sleep(3)
        waited += 3
        try:
            last = page.url
        except Exception:
            print("WINDOW_CLOSED", flush=True)
            break
        if signed_in(page):
            ok = True
            break
        if waited % 30 == 0:  # progress, so a stuck run is visible instead of silently waiting
            print(f"  still waiting for sign-in ({waited}s) at {last[:90]}", flush=True)

    if ok:
        print("LOGIN_OK", flush=True)
    else:
        try:  # diagnostics: makes a detection failure debuggable instead of a blind timeout
            print(f"LAST_URL={last} TITLE={page.title()!r}", flush=True)
        except Exception:
            pass
        print("LOGIN_TIMEOUT_OR_FAILED", flush=True)
    ctx.close()
    sys.exit(0 if ok else 1)
