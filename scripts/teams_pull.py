#!/usr/bin/env python3
"""Headless Microsoft Teams (web) pull — Activity feed (mentions/invites/reactions) + chat
unread list + recent CHANNEL messages across all teams — via the shared M365 profile. No
computer-use, no cursor takeover, no authorize dialog. Reuses $MT_STATE_DIR/o365_profile
(one-time SSO via o365_login.py).
Prints JSON: {signed_in, activity:[...], chats:[...], channels:[...], channels_coverage:{...}}.
{"error":"NOT_SIGNED_IN"} if the SSO session expired; {"error":"PROFILE_LOCKED"} if the shared
profile is held by a concurrent run (single-flight: one retry, then graceful exit).

The channel scrape is ADDITIVE and INDEPENDENTLY GUARDED: it runs after activity+chats, is
wrapped in try/except, and has its own wall-clock budget. If it fails, times out, or is disabled
(TEAMS_SCRAPE_CHANNELS=0), activity[] and chats[] STILL return exactly as before — the only
difference is channels==[] and channels_coverage explains why. Coverage is honest: it never
claims full coverage, records teams_seen / channels_read / channels_skipped / budget_hit."""
import json, os, sys, time, re
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "o365_profile")
TEAMS = "https://teams.cloud.microsoft/v2/"

# Teams web is a heavy SPA: `domcontentloaded` fires long before the app bar exists, and the
# FIRST run on a freshly signed-in profile downloads the entire app bundle. Sleeping a fixed
# amount silently produced "signed in, but the Chat tab wouldn't open / no chats triaged" on
# newly provisioned machines — so wait for a real anchor element with a generous budget.
APP_LOAD_TIMEOUT_S = float(os.environ.get("MT_TEAMS_LOAD_TIMEOUT_S", "90"))
APP_READY_SELECTORS = ("button[aria-label*='Chat']", "[data-tid='app-bar-chat']",
                       "button[aria-label*='Activity']", "[data-tid='app-bar-activity']")
# How long a single click target may take to become visible (cold UI needs more than a beat).
CLICK_VISIBLE_MS = float(os.environ.get("MT_TEAMS_CLICK_TIMEOUT_MS", "5000"))


def wait_app_ready(page, budget_s):
    """Block until the Teams app bar actually renders, or the budget runs out."""
    deadline = time.time() + budget_s
    while time.time() < deadline:
        for sel in APP_READY_SELECTORS:
            try:
                if page.locator(sel).first.is_visible(timeout=1500):
                    return True
            except Exception:
                pass
        page.wait_for_timeout(1500)
    return False

# --- Channel-scrape tunables (env-overridable; safe defaults) -----------------
SCRAPE_CHANNELS = os.environ.get("TEAMS_SCRAPE_CHANNELS", "1") != "0"
CHANNEL_BUDGET_S = float(os.environ.get("TEAMS_CHANNEL_BUDGET_S", "100"))   # wall-clock cap for the WHOLE channel phase
EXPAND_DEADLINE_S = float(os.environ.get("TEAMS_EXPAND_DEADLINE_S", "40"))  # stop expanding collapsed teams past this; reserve the rest for scraping
MAX_CHANNELS = int(os.environ.get("TEAMS_MAX_CHANNELS", "40"))
PER_CHANNEL_MSGS = int(os.environ.get("TEAMS_PER_CHANNEL_MSGS", "15"))
# Tree rows that are NOT teams/channels (buttons/section labels rendered as treeitems).
NON_TEAM_LABELS = {"discover", "see all your teams", "see all channels", ""}

# --- Chat-thread scrape tunables (env-overridable) ----------------------------
# The Chat rail is [role='tree'] [role='treeitem']; LEAF treeitems are conversations
# (parents nest their children, so leaf-only avoids double counting). We open EVERY
# conversation and, per conversation, scroll UP lazy-loading history until we've covered
# everything since its watermark (first run: since TEAMS_CHAT_SINCE, default yesterday-noon),
# capturing each message WITH author + ISO timestamp so triage judges closure off the
# thread's last sender / who-answered — NOT off the activity-feed history. Watermark stores,
# per conversation, the newest message ISO ts seen, so the next run resumes exactly there.
SCRAPE_CHATS = os.environ.get("TEAMS_SCRAPE_CHATS", "1") != "0"
CHAT_BUDGET_S = float(os.environ.get("TEAMS_CHAT_BUDGET_S", "1200"))   # wall-clock cap for the whole chat phase (20 min; correctness over speed)
MAX_CHATS = int(os.environ.get("TEAMS_MAX_CHATS", "200"))             # safety cap on conversations opened (effectively "all")
MAX_SCROLL_ROUNDS = int(os.environ.get("TEAMS_MAX_SCROLL_ROUNDS", "60"))  # per-conversation scroll-up cap (safety vs a huge thread)
PER_CHAT_BUDGET_S = float(os.environ.get("TEAMS_PER_CHAT_BUDGET_S", "120"))  # per-conversation wall-clock cap
WATERMARK = os.path.expanduser(os.environ.get("TEAMS_CHAT_WATERMARK", os.path.join(STATE, "chat_watermark.json")))
# First-look start. TEAMS_CHAT_SINCE (ISO or local wall-clock) wins; otherwise look back
# MT_LOOKBACK_DAYS days — the plugin-wide window shared with the mail and Zoom collectors.
# This applies ONLY the first time a conversation is seen; once it has a watermark, the run
# resumes from that watermark instead (so nothing is missed and nothing is re-triaged).
_SINCE_ENV = os.environ.get("TEAMS_CHAT_SINCE", "").strip()
_LOOKBACK_DAYS = float(os.environ.get("MT_LOOKBACK_DAYS", "3"))
# Rail rows that are pinned views / section labels, not real conversations.
NON_CHAT_LABELS = {"quick views", "mentions", "chats", "", "saved", "meet now"}


def _default_since_iso():
    """First-look start as an ISO-8601 UTC 'Z' string (comparable to time[datetime]):
    TEAMS_CHAT_SINCE when set, else MT_LOOKBACK_DAYS days back."""
    if _SINCE_ENV:
        # Accept a full ISO or a 'YYYY-MM-DDTHH:MM' local wall-clock; normalize to UTC Z.
        try:
            s = _SINCE_ENV.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.astimezone()  # treat naive as local
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except Exception:
            pass
    start = datetime.now().astimezone() - timedelta(days=_LOOKBACK_DAYS)
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
# Tokens that identify a message as sent BY YOU (used to mark last_sender_is_me / closure).
# Set MT_MY_NAME_TOKENS to fragments of your own Teams display name, ';'-separated
# (e.g. "jane doe;jdoe;jane"). If empty, no message is attributed to you and every open
# question is treated as still needing a reply — so configuring this is strongly recommended.
MY_TOKENS = [t.strip().lower() for t in
             os.environ.get("MT_MY_NAME_TOKENS", "").split(";") if t.strip()]


def _is_me(author):
    a = (author or "").lower()
    return any(tok in a for tok in MY_TOKENS)

# Enumerate the left-rail tree (teams + channels) as an ordered flat list.
_JS_TREE = """() => Array.from(document.querySelectorAll("[role='treeitem']")).map((e,i)=>({
    i: i,
    level: parseInt(e.getAttribute('aria-level')||'0',10),
    expanded: e.getAttribute('aria-expanded'),
    // FIRST line only: an EXPANDED team's innerText also contains its child channels (and every
    // row's trailing timestamp/unread badge sits on later lines) — the label is line 1. Using the
    // whole innerText would both pollute the name AND make it volatile (times tick), which would
    // break channel re-location during the scrape loop.
    text: (((e.innerText||'').split('\\n')[0])||'').replace(/\\s+/g,' ').trim().slice(0,80),
    id: e.getAttribute('id')
}))"""

# Read the last N messages of the currently-open channel pane.
_JS_MSGS = """(n) => Array.from(document.querySelectorAll("[data-tid='channel-pane-message']")).slice(-n).map(e => {
    const sub  = e.querySelector("[data-tid='post-message-subheader']");
    const body = e.querySelector("[data-tid='message-body']");
    const t    = e.querySelector('time');
    const ts   = t ? (t.getAttribute('datetime')||t.getAttribute('title')||t.innerText||'').trim() : '';
    let author = '';
    if (sub) {
        // Prefer a dedicated author element; else take the subheader text BEFORE the timestamp
        // ("<Author> <ts> [New|Edited]" — endsWith(ts) fails when a status badge trails the time).
        const an = sub.querySelector("[data-tid*='author'], [id*='author'], [data-tid='message-author-name']");
        if (an && (an.innerText||'').trim()) {
            author = (an.innerText||'').replace(/\\s+/g,' ').trim();
        } else {
            let s = (sub.innerText||'').replace(/\\s+/g,' ').trim();
            if (ts && s.indexOf(ts) > 0)       author = s.slice(0, s.indexOf(ts)).trim();
            else if (ts && s.endsWith(ts))     author = s.slice(0, s.length - ts.length).trim();
            else                                author = s;
        }
    }
    const text = body ? (body.innerText||'').replace(/\\s+/g,' ').trim()
                      : (e.innerText||'').replace(/\\s+/g,' ').trim();
    return {author: author, ts: ts, text: text};
})"""


def signed_out(url):
    u = url.lower()
    return any(k in u for k in ["login.microsoft", "login.live", "/auth?", "signin", "adfs", "okta"])


def _attach_teams(rows):
    """Tag each tree row with the team it belongs to (nearest preceding level-2 team header).
    Level-1 section headers reset the team; a non-team level-2 label clears it."""
    team = None
    for r in rows:
        lvl = r["level"]
        low = r["text"].strip().lower()
        if lvl == 1:
            team = None
        elif lvl == 2:
            team = None if low in NON_TEAM_LABELS else r["text"].strip()
        r["_team"] = team
    return rows


def _channels(rows):
    """Ordered, de-duplicated channel worklist from a tree snapshot."""
    plan, seen = [], set()
    for r in rows:
        low = r["text"].strip().lower()
        if r["level"] >= 3 and low not in NON_TEAM_LABELS and r.get("_team"):
            key = (r["_team"], r["text"].strip())
            if key not in seen:
                seen.add(key)
                plan.append({"team": r["_team"], "channel": r["text"].strip(), "key": key})
    return plan


# Enumerate the LEFT chat rail. Leaf treeitems (no nested treeitem) are conversations.
_JS_CHAT_RAIL = """() => Array.from(document.querySelectorAll("[role='tree'] [role='treeitem']")).map((e,i)=>({
    i: i,
    leaf: !e.querySelector("[role='treeitem']"),
    text: ((e.innerText||'').replace(/\\s+/g,' ').trim()).slice(0,140)
}))"""

# ALL currently-rendered CHAT messages (DOM is a sliding window under virtual scroll, so we
# read the whole window each round and dedupe in Python). author + ISO ts both live on an
# ANCESTOR message-group container, not inside the message element — walk up to find them.
_JS_CHAT_MSGS = """() => Array.from(document.querySelectorAll("[data-tid='chat-pane-message']")).map(e => {
    let author = '', anc = e.parentElement, hops = 0;
    while (anc && hops < 4 && !author) {
        const a = anc.querySelector("[data-tid='message-author-name']");
        if (a) author = (a.innerText||'').replace(/\\s+/g,' ').trim();
        anc = anc.parentElement; hops++;
    }
    let ts = '', a2 = e, h2 = 0;                       // nearest ancestor time[datetime] (ISO UTC)
    while (a2 && h2 < 6 && !ts) {
        const t = a2.querySelector ? a2.querySelector('time[datetime]') : null;
        if (t) ts = t.getAttribute('datetime') || '';
        a2 = a2.parentElement; h2++;
    }
    const body = e.querySelector("[data-tid='message-body']");
    const text = body ? (body.innerText||'').replace(/\\s+/g,' ').trim()
                      : (e.innerText||'').replace(/\\s+/g,' ').trim();
    return {author: author, ts: ts, text: text};
})"""

# Scroll the message viewport to the very top to lazy-load older messages; return scrollHeight.
_JS_SCROLL_UP = """() => {
    const v = document.querySelector("[data-tid='message-pane-list-viewport']")
        || (document.querySelector("[data-tid='message-pane-list-runway']")||{}).parentElement;
    if (!v) return -1;
    v.scrollTop = 0;
    return v.scrollHeight;
}"""

# Scroll the LEFT chat rail down by dy to reveal more virtualized conversations; return scrollTop.
_JS_RAIL_SCROLL = """(dy) => {
    const t = document.querySelector("[role='tree']");
    if (!t) return -1;
    const sc = t.closest('[style*=overflow]') || t.parentElement || t;
    sc.scrollTop = sc.scrollTop + dy;
    return sc.scrollTop;
}"""


def _rail_recent(raw_label, since_date):
    """True if a rail row's trailing activity token is >= since_date (or non-date → bias True).
    The rail is recency-sorted, so the first row that parses to an OLDER date is the stop line."""
    s = " ".join((raw_label or "").split())
    m = re.search(r"(\d{1,2}:\d{2}\s*[AP]M|Yesterday|Today|"
                  r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
                  r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*)\s*$", s, re.I)
    if not m:
        return True                                   # no parseable token → open (bias: never miss)
    tok = m.group(1)
    if re.match(r"\d{1,2}:\d{2}", tok):               # a clock time → today
        return True
    if tok.lower() in ("yesterday", "today"):
        return True
    if re.match(r"(mon|tue|wed|thu|fri|sat|sun)", tok, re.I):   # weekday → within a week
        return True
    dm = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", tok)
    if dm:
        mo, da = int(dm.group(1)), int(dm.group(2))
        if dm.group(3):
            yr = int(dm.group(3)); yr += 2000 if yr < 100 else 0
        else:
            yr = since_date.year
        try:
            from datetime import date
            return date(yr, mo, da) >= since_date
        except Exception:
            return True
    return True


def _load_watermark():
    try:
        with open(WATERMARK) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_watermark(wm):
    try:
        with open(WATERMARK, "w") as f:
            json.dump(wm, f, ensure_ascii=False, indent=0)
    except Exception:
        pass


def _chat_name(rail_text):
    """Strip the trailing activity time from a rail row to get a stable conversation key."""
    import re
    s = " ".join((rail_text or "").split())
    # Drop a trailing time/date token: "... 9:23 AM" | "... 7/20" | "... Yesterday" | weekday.
    s = re.sub(r"\s+(\d{1,2}:\d{2}\s*[AP]M|\d{1,2}/\d{1,2}(/\d{2,4})?|Yesterday|Today|"
               r"Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*$", "", s, flags=re.I)
    return s.strip()[:100]


def _scrape_one_chat(page, since_iso):
    """Scroll UP the open conversation, accumulating & de-duping messages until coverage
    reaches back past since_iso (or the top / a safety cap). Returns messages sorted oldest→newest."""
    acc = {}                                    # fingerprint -> {author, ts, text}
    tc0 = time.monotonic()
    prev_sh, stable, rounds = -1, 0, 0
    while rounds <= MAX_SCROLL_ROUNDS and (time.monotonic() - tc0) < PER_CHAT_BUDGET_S:
        try:
            batch = page.evaluate(_JS_CHAT_MSGS)
        except Exception:
            break
        earliest = None
        for m in batch:
            text = " ".join((m.get("text") or "").split())[:400]
            if len(text) < 1:
                continue
            author = " ".join((m.get("author") or "").split())[:120]
            ts = (m.get("ts") or "").strip()
            fp = (ts + "|" + author[:30] + "|" + text[:70])
            acc[fp] = {"author": author, "ts": ts, "text": text}
            if ts and (earliest is None or ts < earliest):
                earliest = ts
        # Covered back past the watermark? (ISO-UTC strings sort chronologically.)
        if earliest and earliest < since_iso:
            break
        try:
            sh = page.evaluate(_JS_SCROLL_UP)
        except Exception:
            break
        page.wait_for_timeout(1100)
        if sh == prev_sh:                       # scrollHeight stopped growing -> reached top
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
            prev_sh = sh
        rounds += 1
    # Carry author forward across grouped messages that lost their header, then sort by ts.
    msgs = sorted(acc.values(), key=lambda m: m["ts"] or "")
    last_author = ""
    for m in msgs:
        if m["author"]:
            last_author = m["author"]
        elif last_author:
            m["author"] = last_author
        m["is_me"] = _is_me(m["author"])
    return msgs


def scrape_chats(page, click_first):
    """Open EVERY conversation and, per conversation, scroll up until we've read everything
    since its watermark (first run: since yesterday-noon). Returns (threads, coverage). Each
    thread carries messages[] (with ISO ts + is_me), last_sender, last_is_me, new_count,
    since_used. Watermark stores the newest ts per conversation so the next run resumes there.
    Fully budget-bounded + defensive; one bad chat never aborts the rest."""
    cov = {"chats_seen": 0, "chats_read": [], "chats_skipped": [], "budget_hit": False,
           "rail_rows_scanned": 0, "default_since": _default_since_iso()}
    if not SCRAPE_CHATS:
        cov["note"] = "chat-thread scraping disabled (TEAMS_SCRAPE_CHATS=0)"
        return [], cov

    t0 = time.monotonic()
    left = lambda: CHAT_BUDGET_S - (time.monotonic() - t0)
    default_since = cov["default_since"]
    since_date = datetime.fromisoformat(default_since.replace("Z", "+00:00")).astimezone().date()

    if not click_first(["button[aria-label*='Chat']", "[data-tid='app-bar-chat']", "text=Chat"], settle=8000):
        cov["note"] = "could not open the Chat tab"
        return [], cov

    def _leaf_convos():
        """Currently-rendered leaf conversations (fresh indices), excluding pinned/section rows."""
        try:
            rail = page.evaluate(_JS_CHAT_RAIL)
        except Exception:
            return []
        res = []
        for r in rail:
            if not r.get("leaf"):
                continue
            low = r["text"].strip().lower()
            if low in NON_CHAT_LABELS or low.startswith("mentions"):
                continue
            name = _chat_name(r["text"])
            if not name or name.lower() in NON_CHAT_LABELS:
                continue
            res.append({"i": r["i"], "name": name, "rail_text": r["text"].strip()})
        return res

    # The Chat rail renders with a race: sometimes only a handful of rows are present for the
    # first several seconds (observed 6 vs the true 76). Nudge rendering by scrolling the rail
    # and wait until the leaf count has stopped GROWING for a few polls. Re-click Chat if it
    # stays implausibly small. Take the best (max) count seen.
    _RAIL_MIN = int(os.environ.get("TEAMS_RAIL_MIN", "10"))   # plausible floor before we trust the rail
    best = 0
    for attempt in range(3):
        prev, stable = -1, 0
        for _ in range(22):
            n = len(_leaf_convos())
            best = max(best, n)
            if n == prev:
                stable += 1
                if stable >= 3 and n >= _RAIL_MIN:
                    break
                if stable >= 8:                       # settled low — accept (genuinely small mailbox)
                    break
            else:
                stable, prev = 0, n
            try:                                      # nudge lazy render: to bottom, then back to top
                page.evaluate(_JS_RAIL_SCROLL, 1200)
                page.wait_for_timeout(300)
                page.evaluate(_JS_RAIL_SCROLL, -4000)
            except Exception:
                pass
            page.wait_for_timeout(700)
        if best >= _RAIL_MIN:
            break
        # implausibly small — re-open the Chat tab and try again
        click_first(["button[aria-label*='Chat']", "[data-tid='app-bar-chat']", "text=Chat"], settle=6000)
    if os.environ.get("TEAMS_DEBUG") == "1":
        print(f"[dbg] rail stabilized: best_leaf_count={best}", file=sys.stderr)

    wm = _load_watermark()
    threads, processed = [], set()
    stop, no_progress = False, 0

    # Walk the WHOLE rail, scrolling to reveal more rows, until it stops yielding new
    # conversations. We OPEN only rows whose rail activity is >= since (skipping older ones
    # cheaply, without opening) — the rail's TOP is pinned (not recency-sorted), so we cannot
    # stop at the first old date; we must scan to the bottom. Enumeration is cheap; opening is
    # the cost, and only recent conversations are opened.
    _dbg = os.environ.get("TEAMS_DEBUG") == "1"
    while not stop and left() > 12 and no_progress < 5 and len(processed) < MAX_CHATS:
        cand = [c for c in _leaf_convos() if c["name"] not in processed]
        if _dbg:
            print(f"[dbg] while: cand={len(cand)} processed={len(processed)} no_prog={no_progress}", file=sys.stderr)
        made_progress = False
        for c in cand:
            if left() < 12:
                cov["budget_hit"] = True
                stop = True
                break
            processed.add(c["name"])
            made_progress = True
            if not _rail_recent(c["rail_text"], since_date):
                if _dbg:
                    print(f"[dbg]   skip-old: {c['rail_text'][:40]}", file=sys.stderr)
                continue                             # older than `since` → no new messages, skip opening
            if _dbg:
                print(f"[dbg]   OPEN: {c['rail_text'][:40]}", file=sys.stderr)
            since_iso = wm.get(c["name"]) or default_since
            # Re-locate by NAME in a fresh snapshot right before clicking (indices drift).
            try:
                cur = _leaf_convos()
                idx = next((x["i"] for x in cur if x["name"] == c["name"]), None)
                if idx is None:
                    cov["chats_skipped"].append(c["name"])
                    continue
                page.locator("[role='tree'] [role='treeitem']").nth(idx).click(timeout=2500)
                try:
                    page.wait_for_selector("[data-tid='chat-pane-message']", timeout=3500)
                except Exception:
                    pass
                page.wait_for_timeout(500)
                msgs = _scrape_one_chat(page, since_iso)
            except Exception:
                cov["chats_skipped"].append(c["name"])
                continue

            if not msgs:
                cov["chats_read"].append(c["name"] + " (empty)")
                continue
            new_msgs = [m for m in msgs if (m["ts"] or "") > since_iso]
            last = msgs[-1]
            newest_ts = max((m["ts"] for m in msgs if m["ts"]), default=since_iso)
            wm[c["name"]] = newest_ts
            threads.append({
                "name": c["name"], "rail_text": c["rail_text"], "since_used": since_iso,
                "last_sender": last["author"], "last_is_me": last["is_me"],
                "msg_count": len(msgs), "new_count": len(new_msgs),
                "oldest_ts": msgs[0]["ts"], "newest_ts": newest_ts, "messages": msgs,
            })
            cov["chats_read"].append(f"{c['name']} ({len(new_msgs)} new / {len(msgs)} since {since_iso[:10]})")

        no_progress = 0 if made_progress else no_progress + 1
        if not stop:
            try:
                page.evaluate(_JS_RAIL_SCROLL, 500)
                page.wait_for_timeout(900)
            except Exception:
                pass

    cov["chats_seen"] = len(threads)
    cov["rail_rows_scanned"] = len(processed)
    _save_watermark(wm)
    return threads, cov


def scrape_channels(page, click_first):
    """Walk teams -> channels -> recent messages. Returns (channels, coverage).
    Fully budget-bounded and defensive; one bad team/channel never aborts the rest."""
    cov = {"teams_seen": [], "channels_read": [], "channels_skipped": [], "budget_hit": False}
    if not SCRAPE_CHANNELS:
        cov["note"] = "channel scraping disabled (TEAMS_SCRAPE_CHANNELS=0)"
        return [], cov

    t0 = time.monotonic()
    left = lambda: CHANNEL_BUDGET_S - (time.monotonic() - t0)

    # Switch to the Teams tab (aria-label proven; GUID + text as fallbacks).
    if not click_first(
        ["button[aria-label*='Teams']",
         "[data-tid='2a84919f-59d8-4441-a975-2a8c2643b741']",
         "text=Teams"],
        settle=5000,
    ):
        cov["note"] = "could not open the Teams tab"
        return [], cov

    # Enumerate the tree (retry once if it hasn't rendered yet).
    try:
        rows = page.evaluate(_JS_TREE)
        if not rows:
            page.wait_for_timeout(3000)
            rows = page.evaluate(_JS_TREE)
    except Exception as e:
        cov["note"] = f"could not enumerate the Teams tree: {str(e)[:140]}"
        return [], cov
    if not rows:
        cov["note"] = "Teams tree empty / not rendered — cannot enumerate"
        return [], cov

    _attach_teams(rows)
    for r in rows:
        if r["level"] == 2 and r["text"].strip().lower() not in NON_TEAM_LABELS:
            if r["text"].strip() not in cov["teams_seen"]:
                cov["teams_seen"].append(r["text"].strip())
    visible_keys = {c["key"] for c in _channels(rows)}

    # Expand collapsed teams to reveal their channels (budget-gated; leave time to scrape).
    # Walk "first collapsed team" repeatedly; an attempted-set prevents spinning on a stuck one.
    attempted = set()
    while (time.monotonic() - t0) < EXPAND_DEADLINE_S and left() > 12:
        try:
            first = page.locator("[role='treeitem'][aria-level='2'][aria-expanded='false']").first
            if not first.is_visible(timeout=1200):
                break
            txt = first.inner_text(timeout=1200).split("\n")[0].strip()
        except Exception:
            break
        if txt.lower() in NON_TEAM_LABELS or txt in attempted:
            break
        attempted.add(txt)
        try:
            first.scroll_into_view_if_needed(timeout=1500)
            first.click(timeout=2000)
            page.wait_for_timeout(1600)
        except Exception:
            continue

    # Re-enumerate now that (some) teams are expanded; scrape visible channels first (high signal).
    try:
        rows = page.evaluate(_JS_TREE)
    except Exception:
        rows = rows  # fall back to the pre-expansion snapshot
    _attach_teams(rows)
    plan_all = _channels(rows)
    plan = [c for c in plan_all if c["key"] in visible_keys] + \
           [c for c in plan_all if c["key"] not in visible_keys]

    scraped = set()
    channels = []
    for c in plan:
        if len(channels) >= MAX_CHANNELS:
            break
        if left() < 4:                      # not enough time to safely open another channel
            cov["budget_hit"] = True
            break
        if c["key"] in scraped:
            continue

        # Re-locate the channel in a fresh snapshot (tree is stable during scraping, but be safe).
        try:
            cur = _attach_teams(page.evaluate(_JS_TREE))
        except Exception:
            break
        idx = next((r["i"] for r in cur
                    if r["level"] >= 3 and r["text"].strip() == c["channel"] and r.get("_team") == c["team"]),
                   None)
        if idx is None:
            continue                        # vanished from the tree — leave it for channels_skipped

        try:
            loc = page.locator("[role='treeitem']").nth(idx)
            loc.scroll_into_view_if_needed(timeout=1500)
            loc.click(timeout=2500)
            # Wait for the channel pane to render messages (faster than a fixed sleep when ready,
            # more reliable when the channel is heavy); tolerate genuinely-empty channels.
            try:
                page.wait_for_selector("[data-tid='channel-pane-message']", timeout=3500)
            except Exception:
                pass
            page.wait_for_timeout(700)
            raw = page.evaluate(_JS_MSGS, PER_CHANNEL_MSGS)
        except Exception:
            continue                        # one bad channel must not abort the rest; retry-able next run

        scraped.add(c["key"])
        label = f"{c['team']} / {c['channel']}"
        msgs, seen_txt, last_author = [], set(), ""
        for m in raw:
            text = " ".join((m.get("text") or "").split())[:400]
            if len(text) < 2:
                continue
            author = " ".join((m.get("author") or "").split())[:120] or last_author
            if author:
                last_author = author
            sig = text[:80]
            if sig in seen_txt:
                continue
            seen_txt.add(sig)
            msgs.append({"author": author, "ts": " ".join((m.get("ts") or "").split())[:40], "text": text})
        if msgs:
            channels.append({"team": c["team"], "channel": c["channel"], "label": label, "messages": msgs})
            cov["channels_read"].append(label)
        else:
            cov["channels_read"].append(label + " (empty)")

    # Anything enumerated but not opened is an honest skip.
    for c in plan:
        if c["key"] not in scraped:
            cov["channels_skipped"].append(f"{c['team']} / {c['channel']}")

    # Truncation honesty: the tree itself hides teams/channels behind "See all ..." affordances.
    low_texts = {r["text"].strip().lower() for r in rows}
    trunc = []
    if "see all your teams" in low_texts:
        trunc.append("more teams exist behind 'See all your teams' (not enumerated)")
    if "see all channels" in low_texts:
        trunc.append("some teams keep more channels behind 'See all channels' (not fully expanded)")
    if trunc:
        cov["note"] = "; ".join(trunc)
    return channels, cov


def main():
    out = {
        "signed_in": False, "activity": [], "chats": [], "chat_threads": [], "channels": [],
        "chat_threads_coverage": {"chats_seen": 0, "chats_read": [], "chats_skipped": [], "budget_hit": False},
        "channels_coverage": {"teams_seen": [], "channels_read": [], "channels_skipped": [], "budget_hit": False},
    }
    with sync_playwright() as p:
        # Single-flight: the shared profile can't be opened twice. Retry once, then exit gracefully.
        ctx = None
        for attempt in (1, 2):
            try:
                ctx = p.chromium.launch_persistent_context(PROFILE, headless=True, viewport={"width": 1500, "height": 950})
                break
            except Exception as e:
                if attempt == 2:
                    print(json.dumps({"error": "PROFILE_LOCKED", "detail": str(e)[:200]}))
                    return
                time.sleep(5)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(TEAMS, wait_until="domcontentloaded", timeout=60000)
        ready = wait_app_ready(page, APP_LOAD_TIMEOUT_S)
        if not ready:
            # A fresh profile often just needs one reload once the bundle has landed.
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            ready = wait_app_ready(page, APP_LOAD_TIMEOUT_S)
        if signed_out(page.url):
            print(json.dumps({"error": "NOT_SIGNED_IN", "hint": "re-run scripts/o365_login.py"}))
            ctx.close()
            return
        out["signed_in"] = True
        out["app_ready"] = ready
        if not ready:
            out["warning"] = (
                f"Teams web did not finish loading within {APP_LOAD_TIMEOUT_S:.0f}s (tried twice). "
                "A first run on a freshly signed-in profile downloads the whole app — raise "
                "MT_TEAMS_LOAD_TIMEOUT_S and re-run; chats will be empty this run.")
        page.wait_for_timeout(2500)  # brief settle once the app bar is up

        def click_first(selectors, settle=6000):
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=CLICK_VISIBLE_MS):
                        loc.click()
                        page.wait_for_timeout(settle)
                        return True
                except Exception:
                    continue
            return False

        # Activity feed (mentions, invites, reactions)
        click_first(["button[aria-label*='Activity']", "[data-tid='app-bar-activity']", "text=Activity"])
        acts = page.eval_on_selector_all(
            "[role='listitem'], [role='option']",
            "els => els.map(e=>(e.getAttribute('aria-label')||e.innerText||'').trim())"
            ".filter(t=>t.length>10)",
        )
        seen = set()
        for a in acts:
            a = " ".join(a.split())
            if a[:50] not in seen:
                seen.add(a[:50])
                out["activity"].append(a[:300])
            if len(out["activity"]) >= 25:
                break

        # Chat THREADS — open the most-recent conversations and pull full messages WITH author,
        # so triage judges closure by the thread's last sender, not the activity feed.
        # ADDITIVE + INDEPENDENTLY GUARDED: any failure leaves activity[]/chats[] intact.
        # Runs BEFORE the legacy Unread block so the rail isn't pre-filtered to unread only.
        try:
            out["chat_threads"], out["chat_threads_coverage"] = scrape_chats(page, click_first)
        except Exception as e:
            out["chat_threads"] = []
            out["chat_threads_coverage"] = {"chats_seen": 0, "chats_read": [], "chats_skipped": [],
                                             "budget_hit": False, "note": f"chat scrape raised: {str(e)[:160]}"}

        # Chat list — open Chat, then apply the "Unread" filter and read rail items.
        click_first(["button[aria-label*='Chat']", "[data-tid='app-bar-chat']", "text=Chat"], settle=7000)
        click_first(["button[aria-label*='Unread']", "[aria-label*='Unread (']", "text=Unread"], settle=4000)
        chats = page.eval_on_selector_all(
            "[data-tid='chat-pane-item']",
            "els => els.map(e=>(e.innerText||'').trim()).filter(t=>t.length>15)",
        )
        seen = set()
        for c in chats:
            c = " ".join(c.split())
            if c[:60] not in seen:
                seen.add(c[:60])
                out["chats"].append(c[:280])
            if len(out["chats"]) >= 30:
                break

        # Channel messages across all teams — ADDITIVE + INDEPENDENTLY GUARDED.
        # Any failure/timeout here leaves activity[]/chats[] above exactly as they were.
        try:
            out["channels"], out["channels_coverage"] = scrape_channels(page, click_first)
        except Exception as e:
            out["channels"] = []
            out["channels_coverage"] = {
                "teams_seen": [], "channels_read": [], "channels_skipped": [],
                "budget_hit": False, "note": f"channel scrape raised: {str(e)[:160]}",
            }

        ctx.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
