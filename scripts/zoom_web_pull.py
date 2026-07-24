#!/usr/bin/env python3
"""Pull Zoom cloud-recording transcripts AND AI Companion summaries via the web portal
(no API app needed). Reuses the signed-in persistent profile from zoom_web_login.py.
Covers BOTH "My Recordings" and "Shared with me".

Recordings + transcripts (per source):
  My:     <MT_ZOOM_BASE>/recording        -> /recording/detail?meeting_id=... -> Play -> sniff vtt
  Shared: <MT_ZOOM_BASE>/recording/shared -> /recording/detail?...&fromShared -> Play -> sniff vtt

Summaries (AI Companion), via the authenticated REST API (inherits the browser cookies):
  My list:     /rest/meeting/host_summary_list?page=N
  Shared list: /rest/meeting/summary/user/share_with_me?page=N&search=
  Detail:      /rest/meeting/web_view_summary?meetingId=<id>&summaryId=<sortKey>
               -> result.overallSummary / finalSummaryString, stepList / nextStepItems, summaryItemVOs

My Notes (AI-structured meeting notes), captured passively from the notes SPA:
  List:   POST <docs-host>/api/search/file  {"fileFilters":["FILE_FILTER_MEETING_NOTES"], ...}
          — the response also reveals the account's docs host, which differs per account.
  Body:   <docs-host>/doc/<id>, read as rendered text with the toolbar/footer chrome stripped.

Usage: zoom_web_pull.py [--limit 10] [--out DIR] [--headed]
                        [--no-recordings] [--no-summaries] [--no-notes]
Writes:
  <out>/<date>_<topic>.vtt                      transcripts (my + shared)
  <out>/summaries/<date>_<topic>.summary.md     readable summary (overview + next steps + chapters)
  <out>/summaries/<date>_<topic>.summary.json   raw summary result
  <out>/notes/<date>_<title>.note.md            My Notes body
  <out>/index.json                              {recordings:[...], summaries:[...], notes:[...]}
Prints the index to stdout. NEVER uses wait_until='networkidle' on zoom.us pages."""
import argparse
import json
import os
import re
from datetime import datetime
from urllib.parse import quote
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "zoom_profile")
BASE = os.environ.get("MT_ZOOM_BASE", "https://zoom.us").rstrip("/")
# Plugin-wide lookback window, shared with the Teams and mail collectors.
LOOKBACK_DAYS = float(os.environ.get("MT_LOOKBACK_DAYS", "3"))


def slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (s or "meeting")).strip("_")[:60]


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _within_days(when, days):
    """True when a 'Mon D, YYYY H:MM AM' stamp falls inside the window.
    Missing/unparseable stamps are KEPT rather than silently dropped."""
    if not when or days <= 0:
        return True
    try:
        dt = datetime.strptime(" ".join(str(when).split()), "%b %d, %Y %I:%M %p")
    except Exception:
        return True
    return (datetime.now() - dt).total_seconds() <= days * 86400


def _within_days_any(when, days):
    """Same window check as _within_days, but tolerant of whatever shape a timestamp arrives in
    (epoch seconds/millis, ISO-8601, or 'Mon D, YYYY H:MM AM'). Unknown formats are KEPT."""
    if not when or days <= 0:
        return True
    try:  # epoch seconds or millis
        n = float(when)
        if n > 1e12:
            n /= 1000.0
        return (datetime.now().timestamp() - n) <= days * 86400
    except (TypeError, ValueError):
        pass
    s = " ".join(str(when).split())
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return (now - dt).total_seconds() <= days * 86400
    except Exception:
        pass
    return _within_days(s, days)


def _first(d, *keys):
    """First non-empty value among candidate key names (the API's field names are not
    contractual, so probe the plausible ones rather than pinning one)."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


# Doc-viewer chrome, matched as a WHOLE line so it can never eat real note content
# (a substring match on "share" would delete any sentence containing "shared").
_NOTE_CHROME_LINES = {
    # viewer toolbar
    "add to starred", "share", "copy doc link", "view docs activity center", "more options",
    "manual notes", "transcript", "regenerate", "accept all", "reject all", "export", "download",
    # editor placeholders on an empty note. Deliberately excludes common words like "workflow",
    # "next" or "accept", which are perfectly ordinary lines inside a real meeting note.
    "add icon", "page options", "generate summary", "auto generate summary", "close", "try",
    "don't show again", "start typing or generate with ai anytime",
    "workflow", "previous", "next", "reject", "accept",
    # Zoom Docs first-run help panel. Matched as exact whole lines — this panel can render
    # ALONGSIDE a real note, so it must be removed line by line; treating its presence as
    # "the note is empty" threw away a real note's entire body.
    "user guide", "check out these productivity-boosting features",
    "create data tables", "use data tables to keep tasks organized.",
    "share docs and work together", "customize permissions for collaborators.",
    "discover comments", "give feedback and ask questions on the doc.",
    "📚 tutorial", "using zoom docs", "using zoom docs during a meeting",
    "adding tables to zoom docs", "adding media or files to zoom docs",
    "modifying the layout of a doc", "adding, resolving, and deleting comments in a doc",
    "managing zoom docs", "using zoom docs keyboard shortcuts",
    "managing docs in the zoom docs dashboard",
    "using zoom docs content generation and revision with ai companion",
    "zoom docs user permission types", "how to access your zoom docs",
    "how to search for zoom docs", "how to view comments on zoom docs",
    "how to view recent activities in zoom docs", "how to access zoom docs you created",
    "how to access zoom docs shared with you",
    "how to view the list of your favorite zoom docs", "how to access deleted zoom docs",
    "would you like to auto-generate notes summary when finished?",
}
# Long marketing/footer strings that only ever appear as page furniture.
_NOTE_CHROME_SUBSTR = ("skip to main content", "accessibility overview", "contact sales",
                       "request a demo", "plans & pricing", "1.888.799.8854")


def _clean_note_text(txt, title=None):
    """Strip the doc-viewer chrome from a rendered note.

    The viewer prints the title twice — page header, then document header — with the author's
    avatar initials (e.g. "Z(") wedged between them, so plain adjacent-dedup misses it. The
    title is passed in and dropped wherever it appears, since the caller already writes it as
    the file's heading. Headings are wrapped in zero-width characters, which are normalized.
    Everything else is kept, outline included, because triage can use it.
    """
    t = (title or "").strip()
    lines, prev = [], None
    for raw in (txt or "").splitlines():
        s = raw.replace("​", "").replace("﻿", "").strip()
        if len(s) < 2:
            continue
        low = s.lower()
        if low in _NOTE_CHROME_LINES or any(n in low for n in _NOTE_CHROME_SUBSTR):
            continue
        if t and s == t:
            continue
        if len(s) <= 3 and not re.search(r"[\w一-鿿]{2,}", s):
            continue  # avatar initials / stray glyphs such as "Z("
        if s == prev:
            continue
        prev = s
        lines.append(s)
    return "\n".join(lines).strip()


def signed_out(url):
    u = (url or "").lower()
    return "signin" in u or "login" in u


def _anchor_topic(txt):
    """Pull a human topic out of a recording-list anchor's text (drop count / duration / host lines)."""
    for line in (txt or "").splitlines():
        line = line.strip()
        if (not line or re.fullmatch(r"\d+", line) or re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", line)
                or line.startswith("Z(") or len(line) < 3):
            continue
        return line[:120]
    return ""


# ---------- recordings + transcripts (My + Shared) ----------
def collect_recordings(ctx, sources, limit, out, days):
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    results = []
    for source, path in sources:
        try:
            page.goto(f"{BASE}{path}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
        except Exception as e:
            results.append({"source": source, "error": f"nav failed: {str(e)[:150]}"})
            continue
        if signed_out(page.url):
            results.append({"source": source, "error": "NOT_SIGNED_IN"})
            continue
        anchors = page.eval_on_selector_all(
            "a[href*='/recording/detail']",
            "els => els.map(e => ({h:e.getAttribute('href'), t:(e.innerText||'').trim()}))",
        )
        seen, items = set(), []
        for a in anchors:
            h = a.get("h")
            if h and h not in seen:
                seen.add(h)
                items.append((h, a.get("t") or ""))
        stop = False
        for href, anchor_text in items[:limit]:
            url = href if href.startswith("http") else BASE + href
            entry = {"source": source, "link": url, "files": []}
            vtts = []

            def on_resp(r):
                u = r.url.lower()
                if "vtt" in u and ("transcript" in u or "type=cc" in u):
                    try:
                        b = r.body()
                        if b and len(b) > 100:
                            vtts.append((r.url, b))
                    except Exception:
                        pass

            dpage = ctx.new_page()
            try:
                dpage.goto(url, wait_until="domcontentloaded", timeout=60000)
                dpage.wait_for_timeout(8000)
                body = dpage.inner_text("body")
                m = re.search(r"Recordings and Transcripts\n(.+)\n", body)
                topic = ((m.group(1).strip() if m else "") or _anchor_topic(anchor_text)
                         or (dpage.title() or "meeting"))
                dm = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2} [AP]M)", body)
                when = dm.group(1) if dm else ""
                entry["topic"], entry["start_time"] = topic[:120], when
                if not _within_days(when, days):
                    # The recordings list is newest-first, so everything below is older too.
                    entry["skipped"] = f"older than {days}d"
                    stop = True
                else:
                    has_transcript = "Audio transcript" in body
                    entry["has_transcript"] = has_transcript
                    if has_transcript:
                        try:
                            with ctx.expect_page(timeout=15000) as np:
                                dpage.get_by_text("Play", exact=True).first.click()
                            pp = np.value
                            pp.on("response", on_resp)
                            pp.wait_for_timeout(15000)
                            pp.close()
                        except Exception as e:
                            entry["player_error"] = str(e)[:150]
                    base = f"{slug(when) or 'undated'}_{slug(topic)}"
                    for i, (u, b) in enumerate(vtts):
                        suffix = f"_{i}" if i else ""
                        fn = os.path.join(out, f"{base}{suffix}.vtt")
                        with open(fn, "wb") as f:
                            f.write(b)
                        entry["files"].append(fn)
            except Exception as e:
                entry["error"] = str(e)[:200]
            finally:
                dpage.close()
            results.append(entry)
            if stop:
                break
    return results


# ---------- AI Companion summaries (My + Shared) via REST API ----------
def collect_summaries(ctx, scopes, limit, out, days):
    # Warm up the summary SPA so the REST session is fully established.
    try:
        wp = ctx.pages[0] if ctx.pages else ctx.new_page()
        wp.goto(f"{BASE}/user/meeting/summary", wait_until="domcontentloaded", timeout=60000)
        wp.wait_for_timeout(5000)
    except Exception:
        pass

    req = ctx.request
    sdir = os.path.join(out, "summaries")
    os.makedirs(sdir, exist_ok=True)
    results = []
    seen_ids = set()

    for scope, list_url in scopes:
        page_n, got, stop_scope = 1, 0, False
        while got < limit and not stop_scope:
            try:
                r = req.get(f"{BASE}{list_url}{page_n}", timeout=30000)
                data = r.json()
            except Exception as e:
                results.append({"scope": scope, "error": f"list failed: {str(e)[:120]}"})
                break
            result = data.get("result") or {}
            # "My" returns rows as result.data; "Shared with me" wraps them in result.pageResult.data.
            page_result = result.get("pageResult") or result
            items = page_result.get("data") or []
            if not items:
                break
            for it in items:
                if got >= limit:
                    break
                mid, sid = it.get("meetingId"), it.get("sortKey")
                if not mid or (mid, sid) in seen_ids:
                    continue
                seen_ids.add((mid, sid))
                topic = (it.get("topic") or "meeting")
                when = it.get("createTime") or ""
                host = it.get("host") or ""
                if not _within_days(when, days):
                    # Summary lists are newest-first, so the rest of this scope is older too.
                    stop_scope = True
                    break
                entry = {"scope": scope, "topic": topic[:120], "host": host, "created": when,
                         "meeting_number": it.get("meetingNumber"), "doc_share_url": it.get("docShareUrl")}
                try:
                    du = (f"{BASE}/rest/meeting/web_view_summary?meetingId={quote(mid, safe='')}"
                          f"&summaryId={quote(sid or '', safe='')}&from=")
                    res = (req.get(du, timeout=30000).json() or {}).get("result") or {}
                    overview = strip_html(res.get("overallSummary") or res.get("finalSummaryString")
                                          or res.get("summary") or "")
                    steps = res.get("stepList") or [x.get("action", "") for x in (res.get("nextStepItems") or [])]
                    steps = [strip_html(s) for s in steps if s]
                    chapters = []
                    for vo in (res.get("summaryItemVOs") or []):
                        chapters.append({"title": strip_html(vo.get("label") or vo.get("title") or ""),
                                         "content": strip_html(vo.get("summary") or vo.get("content") or "")})
                    entry.update(overview=overview, next_steps=steps, chapters=chapters)

                    base = f"{slug(when) or 'undated'}_{slug(topic)}"
                    with open(os.path.join(sdir, f"{base}.summary.json"), "w") as f:
                        json.dump(res, f, ensure_ascii=False, indent=2)
                    lines = [f"# {topic}", "", f"- Date: {when}", f"- Host: {host}",
                             f"- Meeting: {it.get('meetingNumber')}", f"- Scope: {scope}", ""]
                    if overview:
                        lines += ["## Overview", overview, ""]
                    if steps:
                        lines += ["## Next steps"] + [f"- {s}" for s in steps] + [""]
                    for ch in chapters:
                        if ch["title"] or ch["content"]:
                            lines += [f"## {ch['title']}".rstrip(), ch["content"], ""]
                    mf = os.path.join(sdir, f"{base}.summary.md")
                    with open(mf, "w") as f:
                        f.write("\n".join(lines))
                    entry["file"] = mf
                except Exception as e:
                    entry["error"] = f"detail failed: {str(e)[:120]}"
                results.append(entry)
                got += 1

            if stop_scope:
                break
            total_page = page_result.get("totalPage") or page_result.get("totalPages")
            if (total_page and page_n >= total_page) or len(items) < (page_result.get("pageSize") or 15):
                break
            page_n += 1
    return results


def collect_notes(ctx, days, out, limit):
    """Collect Zoom Notes — the AI-structured meeting notes — from BOTH tabs.

    The two tabs use different endpoints AND different response shapes (both verified live):
      My notes       POST <docs>/api/search/file   {"fileFilters":["FILE_FILTER_MEETING_NOTES"],…}
                     -> {"items": [ …flat file objects… ]}
      Shared with me GET  <docs>/api/file/shared?fileFilters[]=FILE_FILTER_MEETING_NOTES&…
                     -> {"sharedFiles": [ {"id":…, "file": {"id","title","updatedInfo":{…}}} ]}
    A note shared with you does NOT appear under My notes, so covering only the first endpoint
    silently returns nothing.

    Both are captured **passively** from the SPA's own requests rather than re-issued: they
    carry SPA-injected auth headers, and the same responses reveal the account's docs host
    (e.g. `us01docs.zoom.us`), which differs per account — so nothing is hard-coded. Bodies are
    read from `<docs>/doc/<id>` as rendered text with the viewer chrome stripped.
    """
    grabbed = {"my": [], "shared": [], "docs": None}

    def hook(r):
        try:
            u = r.url
            if "/api/search/file" in u:            # My notes  (POST, {"items": [...]})
                grabbed["docs"] = "https://" + u.split("/")[2]
                grabbed["my"].extend((r.json() or {}).get("items") or [])
            elif "/api/file/shared" in u:          # Shared    (GET,  {"sharedFiles": [...]})
                grabbed["docs"] = "https://" + u.split("/")[2]
                grabbed["shared"].extend((r.json() or {}).get("sharedFiles") or [])
        except Exception:
            pass

    # Each tab gets its own page, and we WAIT FOR ITS REQUEST rather than sleeping a fixed
    # amount: a fixed wait then navigating to the next tab silently dropped the first tab's
    # in-flight response, so "My notes" came back empty even when the account had notes.
    errors = []
    for frag, marker in (("#/my_notes", "/api/search/file"),
                         ("#/my_notes/shared", "/api/file/shared")):
        pg = ctx.new_page()
        pg.on("response", hook)
        try:
            with pg.expect_response(lambda r, m=marker: m in r.url, timeout=45000):
                pg.goto(f"{BASE}/notes{frag}", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(3000)  # let the hook finish reading the body
        except Exception as e:
            errors.append(f"{frag}: {str(e)[:110]}")
        finally:
            try:
                pg.close()
            except Exception:
                pass

    docs = grabbed["docs"]
    if not docs:
        note = "; ".join(errors) if errors else "no notes request observed"
        return [{"error": f"notes list not captured ({note})"}]

    def norm(row, scope):
        """Both endpoints wrap the record the same way — {"file": {...}} — even though one
        returns it under "items" and the other under "sharedFiles". Reading the outer object's
        keys yields nothing, which silently dropped every note from the My tab."""
        f = row.get("file") or row
        upd, crt = f.get("updatedInfo") or {}, f.get("createdInfo") or {}
        return {"scope": scope,
                "id": _first(f, "id", "fileId", "docId", "documentId"),
                "title": str(_first(f, "title", "fileName", "name", "topic") or "note"),
                "updated": (upd.get("time") or crt.get("time")
                            or _first(f, "modifiedTime", "updateTime", "updatedAt") or ""),
                "owner": ((upd.get("user") or {}).get("displayName")
                          or (crt.get("user") or {}).get("displayName") or "")}

    rows = ([norm(x, "my") for x in grabbed["my"]]
            + [norm(x, "shared") for x in grabbed["shared"]])

    ndir = os.path.join(out, "notes")
    os.makedirs(ndir, exist_ok=True)
    results, seen = [], set()
    for entry in rows:
        if len(results) >= limit:
            break
        nid = entry.get("id")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        if not _within_days_any(entry.get("updated"), days):
            continue
        entry["title"] = entry["title"].strip()[:120] or "note"
        d = None
        try:
            d = ctx.new_page()
            d.goto(f"{docs}/doc/{nid}", wait_until="domcontentloaded", timeout=45000)
            d.wait_for_timeout(9000)
            entry["text"] = _clean_note_text(d.inner_text("body"), entry["title"])
            if not entry["text"]:
                entry["empty"] = "note has no content yet"
        except Exception as e:
            entry["error"] = f"note body failed: {str(e)[:130]}"
        finally:
            try:
                if d:
                    d.close()
            except Exception:
                pass
        if entry.get("text"):
            base = f"{slug(str(entry.get('updated'))[:10]) or 'undated'}_{slug(entry['title'])}"
            fn = os.path.join(ndir, f"{base}.note.md")
            try:
                header = [f"# {entry['title']}", "", f"- Updated: {entry.get('updated')}",
                          f"- Scope: {entry['scope']}"]
                if entry.get("owner"):
                    header.append(f"- Owner: {entry['owner']}")
                with open(fn, "w") as f:
                    f.write("\n".join(header) + "\n\n" + entry["text"] + "\n")
                entry["file"] = fn
            except Exception as e:
                entry["write_error"] = str(e)[:120]
        results.append(entry)
    if errors:
        results.append({"warning": "; ".join(errors)})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--days", type=float, default=LOOKBACK_DAYS,
                    help="only collect meetings from the last N days (default: MT_LOOKBACK_DAYS)")
    ap.add_argument("--out", default=os.path.join(STATE, "zoom_transcripts"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-recordings", action="store_true")
    ap.add_argument("--no-summaries", action="store_true")
    ap.add_argument("--no-notes", action="store_true", help="skip Zoom My Notes")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    index = {"recordings": [], "summaries": [], "notes": []}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=not args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE}/recording", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        if signed_out(page.url):
            print(json.dumps({"error": "NOT_SIGNED_IN — run zoom_web_login.py first"}))
            ctx.close()
            return

        if not args.no_recordings:
            index["recordings"] = collect_recordings(
                ctx, [("my", "/recording"), ("shared", "/recording/shared")],
                args.limit, args.out, args.days)
        if not args.no_summaries:
            index["summaries"] = collect_summaries(
                ctx,
                [("my", "/rest/meeting/host_summary_list?page="),
                 ("shared", "/rest/meeting/summary/user/share_with_me?search=&page=")],
                args.limit, args.out, args.days)
        if not args.no_notes:
            index["notes"] = collect_notes(ctx, args.days, args.out, args.limit)
        ctx.close()

    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(json.dumps(index, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
