#!/usr/bin/env python3
"""One-time Zoom web sign-in. Opens a real browser window with a persistent
profile under $MT_STATE_DIR/zoom_profile; you complete your Zoom sign-in in it.
Exits LOGIN_OK once the recordings page is reachable signed-in.

Detection avoids hard-coding a host (same lesson as the M365 login: a moved host must not
look like a failed sign-in) and prints progress + diagnostics instead of waiting silently."""
import os
import sys
import time
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "zoom_profile")
BASE = os.environ.get("MT_ZOOM_BASE", "https://zoom.us").rstrip("/")
URL = f"{BASE}/recording"
TIMEOUT_S = float(os.environ.get("MT_LOGIN_TIMEOUT_S", "600"))


def signed_in(page):
    u = (page.url or "").lower()
    if any(k in u for k in ("signin", "/login", "/auth", "okta")) or "/recording" not in u:
        return False
    try:
        if page.locator("text=Recordings").first.is_visible(timeout=2000):
            return True
    except Exception:
        pass
    try:
        body = page.content().lower()
        return "recording" in body and ("topic" in body or "录制" in body or "date" in body)
    except Exception:
        return False


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, viewport={"width": 1280, "height": 850})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("BROWSER_OPEN: complete the Zoom sign-in in the window", flush=True)

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
        if waited % 30 == 0:
            print(f"  still waiting for sign-in ({waited}s) at {last[:90]}", flush=True)

    if ok:
        print("LOGIN_OK", flush=True)
    else:
        try:
            print(f"LAST_URL={last} TITLE={page.title()!r}", flush=True)
        except Exception:
            pass
        print("LOGIN_TIMEOUT_OR_FAILED", flush=True)
    ctx.close()
    sys.exit(0 if ok else 1)
