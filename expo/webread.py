"""Reads an expo list from a web link: normal pages (requests) or JavaScript pages (browser engine)."""
import json, os, re, subprocess, sys, time
from urllib.parse import urlparse
import requests

from . import extract as ex
from .util import clean

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
MAX_PAGES = 200
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(os.path.dirname(HERE), "profiles")


class ReadError(Exception):
    pass


DEBUG_FILE = os.path.join(os.path.dirname(HERE), "last_page.html")
DEBUG_DETAIL_FILE = os.path.join(os.path.dirname(HERE), "last_company_page.html")


def save_debug(html, url, path=None):
    """Keep the last page the tool received, so a problem can be looked at."""
    try:
        with open(path or DEBUG_FILE, "w", encoding="utf-8") as f:
            f.write(f"<!-- {url} -->\n" + html)
    except OSError:
        pass


def page_summary(html):
    """One line: what the tool received."""
    from bs4 import BeautifulSoup
    sp = BeautifulSoup(html, "lxml")
    t = clean(sp.title.get_text(" ")) if sp.title else ""
    return (f"The tool received a page of {len(html) // 1024} KB, title “{t[:60]}”. It contains: "
            f"{len(sp.find_all('table'))} tables, {len(sp.find_all('li'))} list items, "
            f"{len(sp.find_all('iframe'))} frames, {len(sp.find_all('script'))} scripts.")


def load_profile(url):
    d = ex.own_domain(url)
    p = os.path.join(PROFILE_DIR, d + ".json")
    if d and os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def fetch(url, session=None, tries=3):
    s = session or requests.Session()
    last = None
    for i in range(tries):
        try:
            r = s.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403, 404, 410):
                break
        except requests.RequestException as e:
            last = str(e)[:120]
        time.sleep(1 + i)
    raise ReadError(f"Could not open the link ({last}).")


def fill_details(records, sess, progress, notes):
    """Open every company's own page and add what it shows (About, Products, Brochures, contacts ...)."""
    details = [r for r in records if r.get("detail_url")]
    best_score = -1
    for i, r in enumerate(details, 1):
        try:
            dh = fetch(r["detail_url"], sess, tries=2)
            det = ex.parse_detail(dh, r["detail_url"], r["Company Name"])
            ex.merge_detail(r, det)
            score = sum(len(det.get(f, [])) for f in ("Product", "Category", "Brochures", "Press Release", "Video")) \
                + len(det.get("contacts", [])) * 2 + (1 if det.get("About the Company") else 0)
            if score > best_score:      # keep the richest company page, for checking
                best_score = score
                save_debug(dh, r["detail_url"], DEBUG_DETAIL_FILE)
        except ReadError:
            notes.append(f"Details not read for {r['Company Name']}")
        except Exception as e:          # one odd page must not stop the whole reading
            notes.append(f"Details not read for {r['Company Name']} ({type(e).__name__})")
        if i % 5 == 0 or i == len(details):
            progress("detail", f"Company pages: {i} of {len(details)}")
        time.sleep(0.2)


def check_total(html, n, notes, limit=0):
    """Compare with a total the page itself states. Returns True when the list looks incomplete."""
    if limit:
        return False
    try:
        t = ex.stated_total(html)
    except Exception:
        return False
    if t and n < t * 0.97:
        notes.append(f"The page says about {t} companies, the tool read {n}. {t - n} may be missing (or merged as "
                     f"duplicates). Check the end of the list; if many are missing, send the list page from "
                     f"'For your helper'.")
        return True
    return False


def nxt_known_more(html, cur):
    """True when this page is not the last one (it has a Next link or a higher page number)."""
    try:
        return bool(ex.find_next_url(html, cur))
    except Exception:
        return False


def read_static(url, progress=None, profile=None, max_pages=MAX_PAGES, fetch_details=True, limit=0):
    """Reads a list that is in the page HTML. Returns dict(records, year, site, how, notes)."""
    progress = progress or (lambda *a, **k: None)
    profile = profile if profile is not None else load_profile(url)
    sess = requests.Session()
    html = fetch(url, sess)
    first_html = html
    year, site = ex.detect_year(html, url), ex.detect_site_name(html, url)
    save_debug(html, url)
    records, notes, seen_pages, cur, how = [], [], set(), url, "none"
    js_pages = False
    card_sig = None
    if not ex.parse_list(html, url, profile)[0]:          # list may sit in a frame inside the page
        for src in ex.iframe_urls(html, url)[:4]:
            try:
                h2 = fetch(src, sess, tries=1)
            except ReadError:
                continue
            if ex.parse_list(h2, src, profile)[0]:
                html, url = h2, src
                notes.append("The list was inside a frame; read from: " + src)
                break
    page = 0
    while cur and cur not in seen_pages and page < max_pages:
        seen_pages.add(cur)
        page += 1
        if card_sig:                       # same kind of company blocks as page 1 (a short last page too)
            recs, how_p = ex.parse_cards_sig(html, cur, card_sig), "cards"
            if not recs:                   # nothing like page 1 on this page: read it the normal way
                alt, how_alt = ex.parse_list(html, cur, profile)
                if alt:
                    recs, how_p = alt, how_alt
                else:
                    card_sig = None
            # (1 or 2 blocks like on page 1 = a short last page: keep them, do not swap them for guesses)
        else:
            recs, how_p = ex.parse_list(html, cur, profile)
            if how_p == "cards":
                card_sig = ex.card_sig(html)
            elif how_p == "entries":
                card_sig = ex.entries_sig(html, cur)
        if how_p != "none":
            how = how_p
        before = len(records)
        if page == 1:
            first_n = len(recs)
        elif first_n >= 5 and len(recs) < first_n and nxt_known_more(html, cur):
            notes.append(f"Page {page} gave only {len(recs)} companies while page 1 gave {first_n}. "
                         f"Please compare page {page} on the website.")
        records.extend(recs)
        progress("list", f"Page {page}: {len(recs)} companies (total {len(records)})")
        if limit and len(records) >= limit:
            break
        nxt = ex.find_next_url(html, cur)
        if not nxt and recs:                                   # no Next link: follow the page numbers (1 2 3 ...)
            for u in ex.page_number_urls(html, cur):
                if u not in seen_pages and u != cur:
                    nxt = u
                    break
        if not nxt and recs and ex.has_js_pagination(html):
            js_pages = True
        if not nxt or not recs:
            if page == 1 and recs:
                notes.append("Only the first page was read: no 'Next' link or page-number link was found. If the site "
                             "has more pages, they load by JavaScript - use Advanced > reading method > Browser.")
            elif recs == [] and page > 1:
                notes.append(f"Page {page} gave no companies, so reading stopped there.")
            break
        try:
            html = fetch(nxt, sess)
        except ReadError as e:
            notes.append(f"Stopped at page {page + 1}: {e}")
            break
        cur = nxt
        if len(records) == before:
            break
    if page >= max_pages:
        notes.append(f"Stopped after {max_pages} pages (safety limit).")
    bar = ex.letter_bar(first_html, url)
    if bar and bar["urls"] and not limit:                   # A-Z bar with real links: read every letter
        done = set(seen_pages)
        for i, lu in enumerate(bar["urls"][:60], 1):
            if lu in done:
                continue
            done.add(lu)
            try:
                hl = fetch(lu, sess, tries=1)
                got = ex.parse_list_lenient(hl, lu, profile, card_sig)
            except ReadError:
                continue
            records.extend(got)
            progress("list", f"Letter {i} of {len(bar['urls'])}: total {len(records)}")
    records = ex.dedupe_names(records)
    if limit:
        records = records[:limit]
        notes.append(f"Test mode: only the first {limit} companies were read.")
    suspect = check_total(first_html, len(records), notes, limit)
    if bar and not limit and len(records) >= 5 and len(ex.first_letters(records)) <= 3:
        notes.append("This page has an A-Z bar, but the names read start with only: "
                     + ", ".join(sorted(ex.first_letters(records))) + ". Other letters may be missing.")
        suspect = True
    if fetch_details:
        fill_details(records, sess, progress, notes)
    ex.drop_placeholder_logos(records)
    return {"records": records, "year": year, "site": site, "how": how, "notes": notes[:20], "suspect": bool(suspect),
            "js_pages": js_pages}


def read_with_browser(url, progress=None, profile=None, max_pages=MAX_PAGES, limit=0, fetch_details=True):
    """Runs the browser reader (jsrunner.py) as a separate process for the LIST (page 2, 3 ... load by
    JavaScript). Company pages are then read normally (fast)."""
    progress = progress or (lambda *a, **k: None)
    profile = profile if profile is not None else load_profile(url)
    cmd = [sys.executable, os.path.join(HERE, "jsrunner.py"), url, str(max_pages), json.dumps(profile),
           "nodetails", str(limit or 0), DEBUG_FILE]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace")
    result = None
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "progress":
            progress("browser", msg["text"])
        elif msg.get("type") == "result":
            result = msg
    p.wait()
    if result is None:
        err = (p.stderr.read() or "")[-300:]
        raise ReadError("The browser reader could not finish. " + clean(err))
    if result.get("error"):
        raise ReadError(result["error"])
    recs = result["records"]
    if limit:
        recs = recs[:limit]
    for r in recs:
        for k in ("Brochures", "Press Release", "Video", "Product", "Category", "Subcategory", "Services", "extra"):
            r.setdefault(k, [])
        r.setdefault("contacts", [])
    notes = list(result.get("notes", []))
    if limit:
        notes.append(f"Test mode: only the first {limit} companies were read.")
    if fetch_details:
        if _details_need_browser(recs):
            progress("detail", "Company pages are drawn by JavaScript; reading them with the browser (slower)...")
            recs = _browser_details(recs, progress, notes)
        else:
            fill_details(recs, requests.Session(), progress, notes)
    ex.drop_placeholder_logos(recs)
    try:
        with open(DEBUG_FILE, encoding="utf-8") as f:
            check_total(f.read(), len(recs), notes, limit)
    except OSError:
        pass
    return {"records": recs, "year": result.get("year", ""), "site": result.get("site", ""),
            "how": result.get("how", "browser"), "notes": notes[:20]}


def _details_need_browser(recs):
    """True when a plain download of a company page shows no company data (the page is built by JavaScript)."""
    det = [r for r in recs if r.get("detail_url")]
    for r in det[:3]:
        try:
            d = ex.parse_detail(fetch(r["detail_url"], tries=1), r["detail_url"], r["Company Name"])
        except Exception:
            continue
        if any(d.get(f) for f in ("About the Company", "Product", "Category", "Brochures", "Website", "Email",
                                  "Address / Corporate Address")) or d.get("contacts"):
            return False
    return bool(det)


def _browser_details(recs, progress, notes):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recs, f)
        cmd = [sys.executable, os.path.join(HERE, "jsrunner.py"), "-", "0", "{}", "file:" + path, "0", DEBUG_DETAIL_FILE]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             errors="replace")
        result = None
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "progress":
                progress("detail", msg["text"])
            elif msg.get("type") == "result":
                result = msg
        p.wait()
        if result is None:
            notes.append("Company pages could not be read by the browser. " + clean((p.stderr.read() or "")[-200:]))
            return recs
        notes.extend(result.get("notes", []))
        out = result["records"]
        for r in out:
            for k in ("Brochures", "Press Release", "Video", "Product", "Category", "Subcategory", "Services", "extra"):
                r.setdefault(k, [])
            r.setdefault("contacts", [])
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def read_link(url, progress=None, mode="auto", limit=0):
    """mode: auto (normal first; browser if little found or other pages need JavaScript), normal, browser."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not urlparse(url).netloc or "." not in urlparse(url).netloc:
        raise ReadError("This does not look like a web link.")
    if mode == "browser":
        return read_with_browser(url, progress, limit=limit)
    res = read_static(url, progress, limit=limit)
    enough = bool(limit) and len(res["records"]) >= limit
    need_js = mode == "auto" and not enough and (len(res["records"]) < 3 or res.get("js_pages") or res.get("suspect"))
    if need_js:
        if progress:
            progress("browser", "Other pages load by JavaScript; using the browser reader...")
        try:
            res2 = read_with_browser(url, progress, limit=limit)
            if len(res2["records"]) > len(res["records"]):
                res2["notes"].append("The normal reader looked incomplete, so the browser reader was used automatically.")
                return res2
        except ReadError as e:
            res["notes"].append("Browser reader: " + str(e))
        if res.get("js_pages"):
            res["notes"].append("Only the first page of the list could be read; this site loads the other pages "
                                "by JavaScript. Try the Browser reader method.")
    return res
