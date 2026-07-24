#!/usr/bin/env python3
"""One-time Zoom web sign-in. Opens a real browser window with a persistent
profile under $MT_STATE_DIR/zoom_profile; you complete your Zoom SSO in it.
Exits with LOGIN_OK once the recordings page is reachable signed-in."""
import os, sys, time
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "zoom_profile")
BASE = os.environ.get("MT_ZOOM_BASE", "https://zoom.us").rstrip("/")
URL = f"{BASE}/recording"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, viewport={"width": 1280, "height": 850})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("BROWSER_OPEN: complete the SSO sign-in in the window", flush=True)
    deadline = time.time() + 600
    ok = False
    while time.time() < deadline:
        time.sleep(3)
        try:
            u = page.url
        except Exception:
            print("WINDOW_CLOSED", flush=True)
            break
        # Signed-in recordings page: zoom.us/recording without being bounced to signin
        if "zoom.us" in u and "/recording" in u and "signin" not in u and "login" not in u:
            try:
                if page.locator("text=Recordings").first.is_visible(timeout=2000):
                    ok = True
                    break
            except Exception:
                body = page.content()
                if "recording" in body.lower() and ("Topic" in body or "录制" in body or "Date" in body):
                    ok = True
                    break
    if ok:
        print("LOGIN_OK", flush=True)
    else:
        print("LOGIN_TIMEOUT_OR_FAILED", flush=True)
    ctx.close()
    sys.exit(0 if ok else 1)
