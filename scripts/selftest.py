#!/usr/bin/env python3
"""Preflight for the morning-triage plugin. Verifies the venv/Playwright are installed,
the state dir is writable, and the saved browser sessions exist — then prints a JSON report.

Exit codes (so setup.sh and the skill can branch precisely):
  0  ready to run (may still print non-fatal warnings)
  4  Playwright not importable                 -> run setup.sh
  3  Chromium not installed for Playwright      -> run: playwright install chromium
  2  not signed in (no saved browser profile)   -> run the *_login.py scripts once
  1  state dir not writable                     -> check MT_STATE_DIR permissions
"""
import json
import os
import sys

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))


def _plugin_version():
    """Read the version out of the installed code itself.

    A plugin host may keep showing the version recorded at install time even after the code has
    been updated, so the UI number cannot be trusted to tell you what is actually running.
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".claude-plugin", "plugin.json")) as f:
            return json.load(f).get("version", "unknown")
    except Exception:
        return "unknown"


report = {"plugin_version": _plugin_version(), "state_dir": STATE, "checks": {}, "warnings": []}


def check(name, ok, detail=""):
    report["checks"][name] = {"ok": bool(ok), "detail": detail}
    return bool(ok)


# 1) state dir writable
state_ok = False
try:
    os.makedirs(STATE, exist_ok=True)
    t = os.path.join(STATE, ".write_test")
    with open(t, "w") as f:
        f.write("ok")
    os.remove(t)
    state_ok = check("state_dir_writable", True, STATE)
except Exception as e:
    check("state_dir_writable", False, str(e)[:200])

# 2) playwright importable
pw_ok = False
try:
    import playwright  # noqa: F401
    pw_ok = check("playwright_importable", True, getattr(playwright, "__version__", "?"))
except Exception as e:
    check("playwright_importable", False, str(e)[:200])

# 3) chromium installed for playwright
chromium_ok = False
if pw_ok:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
            chromium_ok = check("chromium_installed", bool(path and os.path.exists(path)), path or "")
    except Exception as e:
        check("chromium_installed", False, str(e)[:200])

# 4) saved browser profiles (are the one-time logins done?)
o365 = os.path.join(STATE, "o365_profile")
zoom = os.path.join(STATE, "zoom_profile")
o365_ok = check("o365_profile", os.path.isdir(o365) and bool(os.listdir(o365)), o365)
zoom_ok = check("zoom_profile", os.path.isdir(zoom) and bool(os.listdir(zoom)), zoom)

# 5) non-fatal config warnings
_tok = os.environ.get("MT_MY_NAME_TOKENS", "").strip().lower()
if not _tok or "jane doe" in _tok or "jdoe" in _tok:
    report["warnings"].append(
        "MT_MY_NAME_TOKENS is empty or still a placeholder — run /morning-triage:setup (it "
        "auto-detects your name from Microsoft 365), or set it by hand; otherwise no message is "
        "attributed to you and every open question reads as needs-reply.")
_zb = os.environ.get("MT_ZOOM_BASE", "https://zoom.us").rstrip("/")
if "yourorg" in _zb or "example" in _zb:
    report["warnings"].append(
        "MT_ZOOM_BASE looks like a placeholder — the default https://zoom.us serves recordings and "
        "summaries for most accounts; only override it if your org requires its vanity host.")

# decide exit code (most fundamental failure wins)
if not state_ok:
    code = 1
elif not pw_ok:
    code = 4
elif not chromium_ok:
    code = 3
elif not (o365_ok or zoom_ok):
    code = 2
else:
    code = 0

report["ready"] = code == 0
report["exit_code"] = code
print(json.dumps(report, indent=2, ensure_ascii=False))
sys.exit(code)
