#!/usr/bin/env python3
"""One-time Microsoft 365 web sign-in (shared profile for Outlook + Teams web).

Opens a real browser window with a persistent profile under $MT_STATE_DIR/o365_profile. You
complete your organization's SSO once there, and that session then covers Outlook AND Teams.
Exits LOGIN_OK as soon as a mailbox is actually reachable.

Detection notes — each one cost somebody a failed setup:
  * HOST-AGNOSTIC: Microsoft moves OWA between hosts (outlook.office.com ->
    outlook.cloud.microsoft), so we check "not on a sign-in page + a mailbox rendered"
    instead of hard-coding a domain.
  * EVERY TAB: an SSO flow can land the user in a *new* tab, so all pages in the context are
    checked — not just the one we opened.
  * SESSION PROBE: the user may finish signing in and then navigate somewhere else entirely,
    so every 15s the session is verified in a throwaway tab.
  * On timeout, the URL + title of every tab is printed, so a stuck run is diagnosable
    instead of a blind 10-minute wait.
"""
import json
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
# Cookies that only exist once SSO has completed. This is the sturdiest signal we have,
# because it does NOT depend on which screen Outlook decided to render — a first-ever login
# can land on a welcome/onboarding page whose title and body match none of our UI checks,
# which looked exactly like "signed in but the script never noticed".
AUTH_COOKIES = ("ESTSAUTHPERSISTENT", "ESTSAUTH", "authtoken")


def signed_in_page(pg):
    """True when THIS page is showing a working mailbox."""
    try:
        u = (pg.url or "").lower()
    except Exception:
        return False
    if any(k in u for k in IDP) or "/mail" not in u:
        return False
    try:
        if "outlook" in (pg.title() or "").lower():
            return True
        return any(k in pg.inner_text("body").lower()
                   for k in ("inbox", "new mail", "new message", "收件箱"))
    except Exception:
        return False


def any_signed_in(ctx):
    """SSO can finish in a tab we did not open, so check them all."""
    return any(signed_in_page(pg) for pg in list(ctx.pages))


def has_auth_cookie(ctx):
    """True once the SSO cookie exists — independent of what the UI is showing."""
    try:
        names = {c.get("name", "") for c in ctx.cookies()}
    except Exception:
        return False
    return any(n in names for n in AUTH_COOKIES)


def on_idp(ctx):
    """True while any tab is still sitting on a sign-in page (so we don't call it too early)."""
    for pg in list(ctx.pages):
        try:
            if any(k in (pg.url or "").lower() for k in IDP):
                return True
        except Exception:
            pass
    return False


def session_works(ctx):
    """Probe the mailbox in a throwaway tab, without disturbing the page the user is on."""
    t = None
    try:
        t = ctx.new_page()
        t.goto(URL, wait_until="domcontentloaded", timeout=25000)
        t.wait_for_timeout(4000)
        return signed_in_page(t)
    except Exception:
        return False
    finally:
        try:
            if t:
                t.close()
        except Exception:
            pass


def tab_state(ctx):
    state = []
    for pg in list(ctx.pages):
        try:
            state.append({"url": (pg.url or "")[:120], "title": (pg.title() or "")[:80]})
        except Exception:
            pass
    return state


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=False, viewport={"width": 1280, "height": 880}
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("BROWSER_OPEN: a browser window just opened — complete the Microsoft 365 sign-in "
          "IN THAT WINDOW. It uses its own profile, so signing in from your normal browser "
          "will NOT be picked up. Waiting...", flush=True)

    deadline = time.time() + TIMEOUT_S
    ok, waited = False, 0
    while time.time() < deadline:
        time.sleep(3)
        waited += 3
        if any_signed_in(ctx):
            ok = True
            break
        # Sturdiest check: SSO is done even if Outlook is showing a first-run/onboarding
        # screen we don't recognize. Require that no tab is still on a sign-in page.
        if has_auth_cookie(ctx) and not on_idp(ctx):
            ok = True
            break
        # They may have signed in and then navigated elsewhere — verify the session directly.
        if waited % 15 == 0 and session_works(ctx):
            ok = True
            break
        if waited % 15 == 0:  # rich progress: enough to diagnose a stuck run from the log alone
            print(f"  waiting ({waited}s) auth_cookie={has_auth_cookie(ctx)} on_signin_page={on_idp(ctx)} "
                  f"tabs={[(t['url'][:55], t['title'][:34]) for t in tab_state(ctx)]}", flush=True)

    if ok:
        print("LOGIN_OK", flush=True)
    else:
        print("LAST_STATE: " + json.dumps(tab_state(ctx), ensure_ascii=False), flush=True)
        print("LOGIN_TIMEOUT_OR_FAILED", flush=True)
    ctx.close()
    sys.exit(0 if ok else 1)
