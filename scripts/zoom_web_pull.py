#!/usr/bin/env python3
"""Pull Zoom cloud-recording transcripts via the web portal (no API app needed).
Reuses the signed-in persistent profile created by zoom_web_login.py.

Proven strategy:
  1. Open <MT_ZOOM_BASE>/recording, collect /recording/detail?meeting_id=... links.
  2. On each detail page, read topic/date, then click Play -> player page.
  3. The player loads the transcript via /rec/play/vtt?type=transcript -> sniff & save.

Usage: zoom_web_pull.py [--limit 10] [--out zoom_transcripts] [--headed]
Writes <out>/<date>_<topic>.vtt + index.json; prints the index to stdout."""
import argparse, json, os, re
from playwright.sync_api import sync_playwright

STATE = os.path.expanduser(os.environ.get("MT_STATE_DIR", "~/.morning-triage"))
PROFILE = os.path.join(STATE, "zoom_profile")
BASE = os.environ.get("MT_ZOOM_BASE", "https://zoom.us").rstrip("/")


def slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (s or "meeting")).strip("_")[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--out", default=os.path.join(STATE, "zoom_transcripts"))
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    index = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=not args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE}/recording", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        if "signin" in page.url or "login" in page.url:
            print(json.dumps({"error": "NOT_SIGNED_IN — run zoom_web_login.py first"}))
            ctx.close()
            return

        links = page.eval_on_selector_all(
            "a[href*='/recording/detail']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        hrefs, seen = [], set()
        for h in links:
            if h and h not in seen:
                seen.add(h)
                hrefs.append(h)
        hrefs = hrefs[: args.limit]
        if not hrefs:
            print(json.dumps({"error": "NO_DETAIL_LINKS_FOUND"}))
            ctx.close()
            return

        for href in hrefs:
            url = href if href.startswith("http") else BASE + href
            entry = {"link": url, "files": []}
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
                topic = m.group(1).strip() if m else (dpage.title() or "meeting")
                dm = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2} [AP]M)", body)
                when = dm.group(1) if dm else ""
                entry["topic"], entry["start_time"] = topic[:120], when
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
                    fn = os.path.join(args.out, f"{base}{suffix}.vtt")
                    with open(fn, "wb") as f:
                        f.write(b)
                    entry["files"].append(fn)
            except Exception as e:
                entry["error"] = str(e)[:200]
            finally:
                dpage.close()
            index.append(entry)

        ctx.close()
    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(json.dumps(index, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
