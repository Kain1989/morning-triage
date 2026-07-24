#!/usr/bin/env python3
"""Mark Outlook (OWA) mail as read, headless, via the shared M365 profile.

Why this specific recipe (learned the hard way):
  1. Apply the **Unread filter** (`button[aria-label='Filter']` -> menuitemradio "Unread").
     The message list is virtualized, so a scroll-only pass badly under-counts; filtering
     makes unread rows contiguous.
  2. **Right-click** a row -> menuitem "Mark as read". The hover "Mark as read" button exists
     but is not actionable headless. The context menu is state-dependent: a read row offers
     "Mark as unread" instead — which is why the Unread filter must come first.
     After marking, the row leaves the filtered view, giving clean termination.
  3. Always reset the filter to "All" (in `finally`), and press Escape first — a context menu
     left open overlays the Filter button and makes the reset time out.

This WRITES to your mailbox, so the triage skill only runs it when MT_MARK_MAIL_READ=1.
Use --dry-run to see what would be marked without changing anything, and --keep to leave
specific mail unread (e.g. threads you still owe a reply).

Prints JSON: {marked, skipped, rows, dry_run} or {"error": ...}.
"""
import argparse
import json
import os
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "o365_profile")
OWA = "https://outlook.office.com/mail/"


def _pick(page, name):
    """Click a filter menu entry by accessible name (OWA renders them as menuitemradio)."""
    for role in ("menuitemradio", "menuitem", "option"):
        try:
            page.get_by_role(role, name=name, exact=True).first.click(timeout=3000)
            return True
        except Exception:
            continue
    return False


def _set_filter(page, name):
    try:
        page.locator("button[aria-label='Filter']").first.click(timeout=8000)
        page.wait_for_timeout(1800)
        ok = _pick(page, name)
        page.wait_for_timeout(4000)
        return ok
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="max rows to touch")
    ap.add_argument("--dry-run", action="store_true", help="list what would be marked; change nothing")
    ap.add_argument("--keep", action="append", default=[],
                    help="substring of a row to leave UNREAD (repeatable), e.g. --keep 'Kumar'")
    args = ap.parse_args()

    out = {"marked": 0, "skipped": 0, "rows": [], "dry_run": args.dry_run}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=True, viewport={"width": 1500, "height": 950})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(OWA, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(12000)
            if any(k in page.url.lower() for k in ("login", "signin", "/auth")):
                print(json.dumps({"error": "NOT_SIGNED_IN", "hint": "re-run setup.sh --login-o365"}))
                ctx.close()
                return

            if not _set_filter(page, "Unread"):
                out["warning"] = "could not apply the Unread filter — nothing marked"
            else:
                skipped = 0
                while out["marked"] + skipped < args.limit:
                    rows = page.locator("div[role='option']")
                    n = rows.count()
                    if n == 0 or skipped >= n:
                        break
                    row = rows.nth(skipped)
                    try:
                        label = " ".join(((row.get_attribute("aria-label") or "")[:200]).split())
                    except Exception:
                        label = ""
                    if any(k.lower() in label.lower() for k in args.keep):
                        out["rows"].append({"kept_unread": label})
                        skipped += 1
                        continue
                    if args.dry_run:
                        out["rows"].append({"would_mark": label})
                        skipped += 1  # leave it in place and walk down the list
                        continue
                    try:
                        row.click(button="right", timeout=6000)
                        page.wait_for_timeout(1500)
                        page.get_by_role("menuitem", name="Mark as read").first.click(timeout=5000)
                        page.wait_for_timeout(1800)
                        out["marked"] += 1
                        out["rows"].append({"marked": label})
                    except Exception as e:
                        out["stopped"] = str(e)[:150]
                        break
                out["skipped"] = skipped
        except Exception as e:
            out["error"] = str(e)[:200]
        finally:
            # Always return the mailbox to the All view, even on failure.
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(1200)
                _set_filter(page, "All")
            except Exception:
                pass
            ctx.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
