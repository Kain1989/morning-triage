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

Usage: zoom_web_pull.py [--limit 10] [--out DIR] [--headed] [--no-recordings] [--no-summaries]
Writes:
  <out>/<date>_<topic>.vtt                      transcripts (my + shared)
  <out>/summaries/<date>_<topic>.summary.md     readable summary (overview + next steps + chapters)
  <out>/summaries/<date>_<topic>.summary.json   raw summary result
  <out>/index.json                              {recordings:[...], summaries:[...]}
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--days", type=float, default=LOOKBACK_DAYS,
                    help="only collect meetings from the last N days (default: MT_LOOKBACK_DAYS)")
    ap.add_argument("--out", default=os.path.join(STATE, "zoom_transcripts"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-recordings", action="store_true")
    ap.add_argument("--no-summaries", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    index = {"recordings": [], "summaries": []}
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
        ctx.close()

    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(json.dumps(index, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
