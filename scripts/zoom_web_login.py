#!/usr/bin/env python3
"""One-time Zoom web sign-in. Opens a real browser window with a persistent profile under
$MT_STATE_DIR/zoom_profile; you complete the Zoom sign-in there. Exits LOGIN_OK once the
recordings page is reachable signed-in.

Same detection hardening as the Microsoft 365 login: host-agnostic checks, every tab is
inspected (SSO can finish in a tab we didn't open), the session is probed directly every 15s
in case the user navigated away after signing in, and a timeout prints every tab's URL+title
so a stuck run can be diagnosed instead of just failing.
"""
import json
import os
import sys
import time
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "zoom_profile")
BASE = os.environ.get("MT_ZOOM_BASE", "https://zoom.us").rstrip("/")
URL = f"{BASE}/recording"
TIMEOUT_S = float(os.environ.get("MT_LOGIN_TIMEOUT_S", "600"))
IDP = ("signin", "/login", "/auth", "okta")
# Cookies Zoom only sets once you are signed in. Sturdier than any UI check, because a
# first-ever login can land on a consent/onboarding screen that matches none of them.
AUTH_COOKIES = ("_zm_login_acctype", "_zm_multi_ac", "_zm_kms")


def signed_in_page(pg):
    try:
        u = (pg.url or "").lower()
    except Exception:
        return False
    if any(k in u for k in IDP) or "/recording" not in u:
        return False
    try:
        if pg.locator("text=Recordings").first.is_visible(timeout=2000):
            return True
    except Exception:
        pass
    try:  # the visible-text check returning False is NOT an exception, so this fallback
        body = pg.content().lower()  # must run unconditionally, not from an except branch
        return "recording" in body and ("topic" in body or "录制" in body or "date" in body)
    except Exception:
        return False


def any_signed_in(ctx):
    return any(signed_in_page(pg) for pg in list(ctx.pages))


def has_auth_cookie(ctx):
    """True once Zoom's signed-in cookies exist — independent of what the UI renders."""
    try:
        names = {c.get("name", "") for c in ctx.cookies()}
    except Exception:
        return False
    return any(n in names for n in AUTH_COOKIES)


def on_idp(ctx):
    """True while any tab is still on a sign-in page (so we don't call it too early)."""
    for pg in list(ctx.pages):
        try:
            if any(k in (pg.url or "").lower() for k in IDP):
                return True
        except Exception:
            pass
    return False


def tab_state(ctx):
    state = []
    for pg in list(ctx.pages):
        try:
            state.append({"url": (pg.url or "")[:120], "title": (pg.title() or "")[:80]})
        except Exception:
            pass
    return state


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, viewport={"width": 1280, "height": 850})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("BROWSER_OPEN: a browser window just opened — complete the Zoom sign-in IN THAT "
          "WINDOW. It uses its own profile, so signing in from your normal browser will NOT "
          "be picked up. Waiting...", flush=True)

    deadline = time.time() + TIMEOUT_S
    ok, waited = False, 0
    while time.time() < deadline:
        time.sleep(3)
        waited += 3
        # Same rule as the M365 login: the signed-in cookie is the only hard evidence. A page
        # can render "My Recordings - Zoom" before it redirects to the sign-in page, so a
        # UI-based check can call success on a profile nobody has signed into yet.
        if has_auth_cookie(ctx) and not on_idp(ctx):
            ok = True
            break
        if waited % 15 == 0:  # rich progress: enough to diagnose a stuck run from the log alone
            print(f"  waiting ({waited}s) auth_cookie={has_auth_cookie(ctx)} on_signin_page={on_idp(ctx)} "
                  f"recordings_ui={any_signed_in(ctx)} "
                  f"tabs={[(t['url'][:55], t['title'][:34]) for t in tab_state(ctx)]}", flush=True)

    if ok:
        print("LOGIN_OK", flush=True)
    else:
        print("LAST_STATE: " + json.dumps(tab_state(ctx), ensure_ascii=False), flush=True)
        print("LOGIN_TIMEOUT_OR_FAILED", flush=True)
    ctx.close()
    sys.exit(0 if ok else 1)
