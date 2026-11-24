"""Browser reader for JavaScript sites. Run as a separate process (keeps memory clean).
usage: jsrunner.py URL MAX_PAGES PROFILE_JSON    -> prints JSON lines (progress / result)"""
import json, sys, time, re, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def out(o):
    print(json.dumps(o, ensure_ascii=True), flush=True)     # plain ASCII: safe on every Windows code page


def details_main(path, debug_path):
    """Open every company page in the browser (sites whose company pages are drawn by JavaScript)."""
    from expo import extract as ex
    from playwright.sync_api import sync_playwright
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    det = [r for r in records if r.get("detail_url")]
    notes, best = [], -1
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
        ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
                            viewport={"width": 1280, "height": 900})
        ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
        pg = ctx.new_page(); pg.set_default_timeout(30000)
        for i, r in enumerate(det, 1):
            try:
                pg.goto(r["detail_url"], wait_until="domcontentloaded")
                try:
                    pg.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                pg.wait_for_timeout(1200)
                html = pg.content()
                d = ex.parse_detail(html, r["detail_url"], r["Company Name"])
                ex.merge_detail(r, d)
                score = sum(len(d.get(f, [])) for f in ("Product", "Category", "Brochures", "Press Release", "Video")) \
                    + len(d.get("contacts", [])) * 2 + (1 if d.get("About the Company") else 0)
                if score > best and debug_path:
                    best = score
                    try:
                        with open(debug_path, "w", encoding="utf-8") as f:
                            f.write(f"<!-- rendered by browser: {r['detail_url']} -->\n" + html)
                    except OSError:
                        pass
            except Exception:
                notes.append(f"Details not read for {r['Company Name']}")
            if i % 5 == 0 or i == len(det):
                out({"type": "progress", "text": f"Company pages (browser): {i} of {len(det)}"})
        b.close()
    out({"type": "result", "records": records, "notes": notes[:20]})


def main():
    if len(sys.argv) > 4 and sys.argv[4].startswith("file:"):
        return details_main(sys.argv[4][5:], sys.argv[6] if len(sys.argv) > 6 else "")
    url, max_pages, profile = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3] or "{}")
    no_details = len(sys.argv) > 4 and sys.argv[4] in ("nodetails", "listonly")
    from_file = sys.argv[4][5:] if len(sys.argv) > 4 and sys.argv[4].startswith("file:") else ""
    limit = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5].isdigit() else 0
    debug_path = sys.argv[6] if len(sys.argv) > 6 else ""
    from expo import extract as ex
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        out({"type": "result", "error": "Browser engine (Playwright) is not installed. Run install.bat again."}); return
    notes = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
        ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
                            viewport={"width": 1280, "height": 900})
        ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
        pg = ctx.new_page()
        pg.set_default_timeout(30000)
        try:
            pg.goto(url, wait_until="domcontentloaded")
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
        except Exception as e:
            out({"type": "result", "error": f"Could not open the link ({str(e)[:100]})."}); b.close(); return
        pg.wait_for_timeout(2500)           # let the page draw its list
        html0 = pg.content()
        if debug_path:                       # keep the page as the browser built it (for checking)
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(f"<!-- rendered by browser: {url} -->\n" + html0)
            except OSError:
                pass
        year, site = ex.detect_year(html0, url), ex.detect_site_name(html0, url)
        records, seen_keys, how = [], set(), "browser"
        stale = 0
        for step in range(max_pages):
            html = pg.content()
            recs, h = ex.parse_list(html, pg.url, profile)
            new = 0
            for r in recs:
                k = (r["Company Name"].lower(), r.get("detail_url", ""))
                if k not in seen_keys:
                    seen_keys.add(k); records.append(r); new += 1
            out({"type": "progress", "text": f"Step {step + 1}: {new} new, total {len(records)}"})
            stale = stale + 1 if new == 0 else 0
            if stale >= 2 or (limit and len(records) >= limit):
                break
            moved = False
            for sel in ["a.ajax-pagination-link:has-text('»')", "a.ajax-pagination-link:has-text('›')", "a[rel=next]", "button:has-text('Load more')", "a:has-text('Load more')",
                        "button:has-text('Show more')", "button:has-text('View more')", "a:has-text('Next')",
                        "button:has-text('Next')", "a.page-link:has-text('»')", "a.page-link:has-text('›')", "li.next a", "a[aria-label*=Next i]", "button[aria-label*=Next i]", "button.mat-paginator-navigation-next"]:
                try:
                    el = pg.query_selector(sel)
                    if el and el.is_visible() and el.is_enabled():
                        cls = (el.get_attribute("class") or "") + (el.evaluate("e=>e.parentElement?e.parentElement.className:''") or "")
                        if "disabled" in cls.lower():
                            continue
                        el.click(); moved = True
                        pg.wait_for_timeout(1500)
                        break
                except Exception:
                    continue
            if not moved:
                # endless scrolling: bring the "load more" trigger / the page end into view, then wait (up to 8 s)
                # until the page has grown
                size = "document.getElementsByTagName('*').length"
                before = pg.evaluate(size)
                # the trigger must really be on screen for a moment: go to it, then walk down to the end in small steps
                pg.evaluate("""() => { const t = document.querySelector('[class*=infinitescroll], [class*=infinite-scroll], [data-infinite]');
                               if (t) t.scrollIntoView({block: 'center'}); }""")
                pg.wait_for_timeout(700)
                for _ in range(40):
                    atend = pg.evaluate("""() => { window.scrollBy(0, Math.max(300, window.innerHeight * 0.7));
                               return window.innerHeight + window.scrollY >= document.body.scrollHeight - 5; }""")
                    pg.wait_for_timeout(120)
                    if atend:
                        break
                pg.mouse.wheel(0, 3000)
                grew = False
                for _ in range(16):
                    pg.wait_for_timeout(500)
                    if pg.evaluate(size) != before:
                        grew = True
                        break
                if not grew:
                    break
        # A-Z bar made of buttons / script links: click every letter and read what it shows
        LET_JS = """(i) => { const els=[...document.querySelectorAll('a,button,li,span')].filter(e=>/^[A-Za-z0-9]$/.test(e.textContent.trim()) && !e.querySelector('a,button,li'));
          const g=new Map(); els.forEach(e=>{const c=e.closest('ul,ol,div,nav,p,td')||document.body; if(!g.has(c)) g.set(c,[]); g.get(c).push(e);});
          let best=[]; g.forEach(v=>{ if(v.length>best.length) best=v; });
          if(best.length<15) return 0;
          if(i>=0){ const el=best[i]; (el.closest('a,button')||el).click(); }
          return best.length; }"""
        try:
            nlet = pg.evaluate(LET_JS, -1) if not limit else 0
        except Exception:
            nlet = 0
        if nlet:
            out({"type": "progress", "text": f"A-Z bar found ({nlet} letters); reading every letter..."})
            for i in range(nlet):
                try:
                    pg.evaluate(LET_JS, i); pg.wait_for_timeout(1200)
                    recs = ex.parse_list_lenient(pg.content(), pg.url, profile)
                except Exception:
                    continue
                new = 0
                for r in recs:
                    k = (r["Company Name"].lower(), r.get("detail_url", ""))
                    if k not in seen_keys:
                        seen_keys.add(k); records.append(r); new += 1
                out({"type": "progress", "text": f"Letter {i + 1} of {nlet}: {new} new, total {len(records)}"})
        records = ex.dedupe_names(records)
        det = [] if no_details else [r for r in records if r.get("detail_url")]
        for i, r in enumerate(det, 1):
            try:
                pg.goto(r["detail_url"], wait_until="domcontentloaded")
                try:
                    pg.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                ex.merge_detail(r, ex.parse_detail(pg.content(), r["detail_url"], r["Company Name"]))
            except Exception:
                notes.append(f"Details not read for {r['Company Name']}")
            if i % 5 == 0 or i == len(det):
                out({"type": "progress", "text": f"Company pages: {i} of {len(det)}"})
        b.close()
    ex.drop_placeholder_logos(records)
    out({"type": "result", "records": records, "year": year, "site": site, "how": how, "notes": notes[:20]})


if __name__ == "__main__":
    main()
