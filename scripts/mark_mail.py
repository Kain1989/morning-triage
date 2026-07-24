#!/usr/bin/env python3
"""Bring the Outlook (OWA) inbox in line with what triage decided, headless.

The plugin reads your mail for you, so anything it digested should stop demanding attention,
and only what still needs YOUR action should stand out:
  * everything triaged      -> marked READ
  * items you must act on   -> left, or set back to, UNREAD so they stay visible

Recipe notes (each one is required, all learned the hard way):
  * Apply the **Unread filter** first (`button[aria-label='Filter']` -> menuitemradio "Unread").
    The message list is virtualized, so a scroll-only pass badly under-counts.
  * Use the **right-click** context menu; the hover button is not actionable headless. The menu
    is state-dependent — an unread row offers "Mark as read", a read row offers "Mark as unread"
    — which is exactly why the filter has to be applied first.
  * Always reset the filter to "All" in `finally`, after pressing Escape: a context menu left
    open overlays the Filter button and makes the reset time out.

Usage:
  mark_mail.py                       # mark all unread mail read
  mark_mail.py --unread "Pacita"     # ...except these, which need action: keep/set them UNREAD
  mark_mail.py --dry-run             # show what would change, change nothing

Prints JSON {marked_read, marked_unread, kept, rows, dry_run} or {"error": ...}.
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


def _label(row):
    try:
        return " ".join(((row.get_attribute("aria-label") or "")[:200]).split())
    except Exception:
        return ""


def _ctx_action(page, row, item):
    """Right-click a row and choose a context-menu entry. True on success."""
    try:
        row.click(button="right", timeout=6000)
        page.wait_for_timeout(1500)
        page.get_by_role("menuitem", name=item).first.click(timeout=5000)
        page.wait_for_timeout(1600)
        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="max rows to touch per phase")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    ap.add_argument("--unread", action="append", default=[],
                    help="substring of mail that still needs your action — keep/set it UNREAD (repeatable)")
    args = ap.parse_args()
    needs_action = [u.lower() for u in args.unread if u.strip()]

    out = {"marked_read": 0, "marked_unread": 0, "kept": 0, "rows": [], "dry_run": args.dry_run}
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

            # Phase 1 — what triage digested becomes READ; skip anything that needs action.
            if not _set_filter(page, "Unread"):
                out["warning"] = "could not apply the Unread filter — nothing marked read"
            else:
                skipped = 0
                while out["marked_read"] + skipped < args.limit:
                    rows = page.locator("div[role='option']")
                    n = rows.count()
                    if n == 0 or skipped >= n:
                        break
                    row = rows.nth(skipped)
                    lbl = _label(row)
                    if any(k in lbl.lower() for k in needs_action):
                        out["rows"].append({"kept_unread": lbl})
                        out["kept"] += 1
                        skipped += 1
                        continue
                    if args.dry_run:
                        out["rows"].append({"would_mark_read": lbl})
                        skipped += 1
                        continue
                    if _ctx_action(page, row, "Mark as read"):
                        out["marked_read"] += 1
                        out["rows"].append({"marked_read": lbl})
                    else:
                        out["stopped"] = "could not mark a row read"
                        break

            # Phase 2 — items needing action that were ALREADY read go back to UNREAD.
            if needs_action:
                _set_filter(page, "All")
                rows = page.locator("div[role='option']")
                for i in range(min(rows.count(), 60)):
                    if out["marked_unread"] >= args.limit:
                        break
                    row = rows.nth(i)
                    lbl = _label(row)
                    low = lbl.lower()
                    if not any(k in low for k in needs_action) or low.startswith("unread"):
                        continue  # not an action item, or already unread
                    if args.dry_run:
                        out["rows"].append({"would_mark_unread": lbl})
                        continue
                    if _ctx_action(page, row, "Mark as unread"):
                        out["marked_unread"] += 1
                        out["rows"].append({"marked_unread": lbl})
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
