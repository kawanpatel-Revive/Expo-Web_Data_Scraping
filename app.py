"""Expo Data Tool - browser screen (Streamlit). Runs only on this laptop: http://localhost:8601"""
import json, os, sys
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expo import webread, filesread, pipeline
from expo.client import AppsScriptClient, ClientError

HERE = os.path.dirname(os.path.abspath(__file__))
NEW_YEAR = "➕ Type a new year…"

st.set_page_config(page_title="Expo Data Tool", page_icon="📋", layout="wide")


# ------------------------------------------------------------------ setup
def load_config():
    p = os.path.join(HERE, "config.json")
    if not os.path.exists(p):
        return None, "config.json is missing. Copy config.example.json to config.json and fill in the two values."
    try:
        c = json.load(open(p, encoding="utf-8"))
    except ValueError:
        return None, "config.json is not written correctly (check commas and quotes)."
    if not c.get("apps_script_url") or not c.get("secret") or "PASTE" in c["apps_script_url"] + c["secret"]:
        return None, "Please fill apps_script_url and secret in config.json."
    return c, ""


def get_client():
    if st.session_state.get("client_override") is not None:   # used by tests
        return st.session_state["client_override"]
    cfg, err = load_config()
    if err:
        st.error(err)
        st.stop()
    return AppsScriptClient(cfg["apps_script_url"], cfg["secret"])


def memory_text():
    try:
        import psutil
        me = psutil.Process().memory_info().rss / 1e6
        vm = psutil.virtual_memory()
        return f"This tool: {me:,.0f} MB · Laptop memory used: {vm.percent:.0f}%"
    except Exception:
        return ""


def sheet_link(url):
    """Link to the Sheet. If config.json has "account_email", the link opens with THAT Google account."""
    cfg, _ = load_config()
    acc = (cfg or {}).get("account_email", "").strip()
    if acc and "@" in acc:
        url += ("&" if "?" in url else "?") + "authuser=" + acc
    st.markdown(f"[Open the Google Sheet]({url})")


def preview_table(records):
    """Short summary of what was read (the Google Sheet gets all 35 columns)."""
    cut = lambda t, n: (t[:n] + "…") if len(t) > n else t
    rows = []
    for r in records:
        rows.append({
            "Company": r.get("Company Name", ""), "Country": r.get("Country", ""),
            "Website": r.get("Website", ""), "Email": r.get("Email", ""),
            "About": cut(r.get("About the Company", ""), 60),
            "Products": cut("; ".join(r.get("Product", [])), 60),
            "Category": cut("; ".join(r.get("Category", [])), 60),
            "Press Release": cut(" | ".join(r.get("Press Release", [])), 50),
            "Videos": cut(" | ".join(r.get("Video", [])), 50), "Contacts": len(r.get("contacts", [])),
            "Extra": cut(" | ".join(r.get("extra", [])), 50),
        })
    return pd.DataFrame(rows)


def clear_link():
    """Button: empty the link box, the pasted text and the preview."""
    for k in ("link1", "paste1"):
        st.session_state[k] = ""
    st.session_state["pending"] = None
    st.session_state["done"] = None


def do_save(client, pend, sheet_name, year):
    bar = st.progress(0.0, text="Saving to Google Sheet…")
    try:
        line, stats, url = pipeline.save_records(
            client, sheet_name, year, pend["records"], pend["source"],
            progress=lambda n, t: bar.progress(n / max(t, 1), text=f"Saving… {n} of {t}"))
    except (ClientError, ValueError) as e:
        bar.empty()
        st.error(f"Not saved completely: {e}  — Press Save again; companies already saved will not be doubled.")
        return
    bar.empty()
    st.session_state["pending"] = None
    st.session_state["done"] = {"line": line, "url": url, "sheet": sheet_name, "year": year,
                                "merged": stats.get("merged_list", [])}
    if pend.get("kind") == "web":
        st.session_state["_clear_link"] = True              # link box becomes empty for the next link
    st.rerun()


def show_done():
    d = st.session_state.get("done")
    if d:
        st.success(f"Saved in “{d['sheet']}” → tab “{d['year']}”. {d['line']}")
        sheet_link(d["url"])
        if d.get("merged"):
            with st.expander(f"Why are there fewer rows than companies read? ({len(d['merged'])} joined)"):
                st.caption("These companies of the list were joined with another row because they have the same "
                           "website or the same / very similar name. Their extra details were added to that row.")
                st.dataframe([{"In the list": a, "Joined with row": b, "Because": c} for a, b, c in d["merged"]],
                             use_container_width=True, hide_index=True)
        if st.button("OK, clear this message"):
            st.session_state["done"] = None
            st.rerun()


# ------------------------------------------------------------------ layout
st.title("📋 Expo Data Tool")
with st.sidebar:
    st.header("Status")
    st.caption(memory_text())
    if st.button("Test connection to Google"):
        try:
            r = get_client().ping()
            st.success(f"Connected. Drive folder: {r.get('folder', '')}")
        except ClientError as e:
            st.error(str(e))
    st.caption("Laptop must stay on and online while reading. Keep this browser tab open.")

for k in ("pending", "done"):
    st.session_state.setdefault(k, None)
show_done()

tab1, tab2 = st.tabs(["1️⃣  Expo list link", "2️⃣  Add more data (PDF, card, Excel, Sheet…)"])

# ------------------------------------------------------------------ tab 1
with tab1:
    st.write("Paste the exhibitor-list link of an expo website. You will see a preview first. "
             "Nothing is saved until you press **Save**.")
    if st.session_state.pop("_clear_link", False):          # after a successful Save: empty the link box
        st.session_state["link1"] = ""
        st.session_state["paste1"] = ""
    lc1, lc2 = st.columns([9, 1], vertical_alignment="bottom")
    link = lc1.text_input("Exhibitor list link", key="link1", placeholder="https://…")
    lc2.button("✖ Clear", key="clear1", help="Empty the link box and the preview", on_click=clear_link)
    mode = "auto"
    with st.expander("Advanced: reading method (leave as Automatic)"):
        mode_label = st.radio("Reading method", ["Automatic (recommended)", "Normal pages only", "Browser reader (JavaScript sites)"],
                              horizontal=True, key="mode1")
        mode = {"Automatic (recommended)": "auto", "Normal pages only": "normal"}.get(mode_label, "browser")
    test_on = st.checkbox("Test mode: read only the first 20 companies (fast)", key="test1")
    if st.button("Read the list", type="primary", disabled=not link.strip()):
        st.session_state["done"] = None
        box = st.status("Reading… (this can take several minutes for big lists)", expanded=True)
        try:
            res = webread.read_link(link, progress=lambda kind, text: box.write(text), mode=mode, limit=20 if test_on else 0)
            if not res["records"]:
                box.update(label="Nothing found", state="error")
                st.session_state["pending"] = None
                st.error("No companies could be read from this link. If the website does not allow automatic reading, "
                         "save the page as PDF (Ctrl+P) and upload it in tab 2.")
                try:
                    dbg = open(webread.DEBUG_FILE, encoding="utf-8").read()
                    st.info(webread.page_summary(dbg))
                    st.download_button("⬇ Download this page (send it to your helper)", dbg.encode("utf-8"),
                                       file_name="last_page.html", mime="text/html")
                except OSError:
                    pass
            else:
                box.update(label=f"{len(res['records'])} companies read", state="complete")
                st.session_state["pending"] = {"kind": "web", "records": res["records"], "site": res["site"],
                                                "year": res["year"], "notes": res["notes"], "source": link.strip()}
        except webread.ReadError as e:
            box.update(label="Could not read", state="error")
            st.session_state["pending"] = None
            st.error(str(e))
    with st.expander("Link does not work? Copy the list from the web page and paste it here"):
        st.caption("Open the page, select the list with the mouse (or Ctrl+A), copy (Ctrl+C), paste below. "
                   "One company per line. Lines copied from a table (with tabs) are split into columns.")
        pasted = st.text_area("Pasted list", height=160, key="paste1")
        if st.button("Use this list", disabled=not pasted.strip(), key="usepaste"):
            import re as _re
            recs = filesread.records_from_pasted(pasted)
            if not recs:
                st.error("No company names found in the text.")
            else:
                from expo.extract import own_domain
                ym = _re.search(r"(?<!\d)(20\d\d)(?!\d)", link or "")
                st.session_state["pending"] = {
                    "kind": "web", "records": recs, "notes": ["Read from pasted text."],
                    "site": (own_domain(link).split(".")[0].title() if link.strip() else ""),
                    "year": ym.group(1) if ym else "", "source": link.strip() or "pasted list"}
                st.session_state["done"] = None
    pend = st.session_state["pending"]
    if pend and pend["kind"] == "web":
        st.subheader(f"Preview: {len(pend['records'])} companies")
        c1, c2 = st.columns(2)
        site = c1.text_input("Website name (Google Sheet name)", pend["site"], key="site1")
        year = c2.text_input("Year (tab name)", pend["year"], key="year1")
        for n in pend["notes"]:
            st.warning(n)
        st.dataframe(preview_table(pend["records"]), width="stretch", height=300)
        with st.expander("See all details of one company (what will go into the Sheet)"):
            pick = st.selectbox("Company", [r.get("Company Name", "") for r in pend["records"]], key="pick1")
            rec = next((r for r in pend["records"] if r.get("Company Name", "") == pick), None)
            if rec:
                show = {k: v for k, v in rec.items() if v and k not in ("detail_url",)}
                st.json(show)
        few = len(pend["records"]) < 5
        if few:
            st.warning("Very few companies were found. This site may need a special rule. Open the box below, "
                       "download the pages and send them to your helper.")
        with st.expander("For your helper: pages the tool saw (only if something is missing)", expanded=few):
            for lbl, fp, fn in (("Download the list page", webread.DEBUG_FILE, "last_page.html"),
                                ("Download a company page", webread.DEBUG_DETAIL_FILE, "last_company_page.html")):
                try:
                    st.download_button("⬇ " + lbl, open(fp, encoding="utf-8").read().encode("utf-8"),
                                       file_name=fn, mime="text/html", key="dl_" + fn)
                except OSError:
                    st.caption(f"{lbl}: not available.")
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("✅ Save", type="primary", key="save1"):
            do_save(get_client(), pend, site, year)
        if b2.button("❌ Cancel", key="cancel1"):
            st.session_state["pending"] = None
            st.rerun()

# ------------------------------------------------------------------ tab 2
with tab2:
    st.write("Add data of a company or many companies into an existing Sheet. Matching companies get the new "
             "data in the same row; new companies are added as new rows.")
    client = None
    try:
        client = get_client()
        sheets = client.list_sheets()
    except ClientError as e:
        sheets = []
        st.error(str(e))
    if not sheets:
        st.info("No Google Sheet yet. First read an expo list in tab 1.")
    else:
        names = {s["name"]: s for s in sheets}
        c1, c2 = st.columns(2)
        sname = c1.selectbox("Website (Google Sheet)", list(names), key="sheet2")
        tabs = client.list_tabs(names[sname]["id"])
        ysel = c2.selectbox("Year (tab)", tabs + [NEW_YEAR], key="year2")
        year2 = st.text_input("New year", key="newyear2") if ysel == NEW_YEAR else ysel
        kind = st.radio("What do you have?", ["Upload files", "Google Sheet link", "Google Drive file link"],
                        horizontal=True, key="kind2")
        files, glink = [], ""
        if kind == "Upload files":
            files = st.file_uploader("PDF, brochure, flyer, photo, screenshot, visiting card, Excel, CSV",
                                     accept_multiple_files=True,
                                     type=["pdf", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "xlsx", "xls", "csv"],
                                     key="files2")
        else:
            glink = st.text_input("Link", key="glink2", placeholder="https://docs.google.com/…")
        ready = bool(files) if kind == "Upload files" else bool(glink.strip())
        if st.button("Read the data", type="primary", disabled=not ready, key="read2"):
            st.session_state["done"] = None
            records, notes, source = [], [], ""
            box = st.status("Reading…", expanded=True)
            try:
                if kind == "Upload files":
                    for f in files:
                        box.write(f"Reading {f.name}…")
                        try:
                            recs, n = filesread.read_upload(f.name, f.getvalue())
                        except Exception as e:
                            notes.append(f"{f.name}: could not be read ({str(e)[:100]})")
                            continue
                        for r in recs:
                            r["_src"] = f.name
                        records += recs
                        notes += [f"{f.name}: {x}" for x in n]
                elif kind == "Google Sheet link":
                    recs, notes = filesread.read_google_sheet(client, glink.strip())
                    records, source = recs, glink.strip()
                else:
                    recs, notes = filesread.read_drive_file(client, glink.strip())
                    records, source = recs, glink.strip()
                box.update(label=f"{len(records)} companies read", state="complete" if records else "error")
            except ClientError as e:
                box.update(label="Could not read", state="error")
                st.error(str(e))
                records = []
            st.session_state["pending"] = ({"kind": "more", "records": records, "notes": notes, "source": source,
                                            "files": [f.name for f in files]} if records else None)
            if not records and not notes:
                st.error("Nothing could be read.")
            for n in notes:
                if not records:
                    st.warning(n)
        pend = st.session_state["pending"]
        if pend and pend["kind"] == "more":
            st.subheader(f"Preview: {len(pend['records'])} companies")
            for n in pend["notes"]:
                st.warning(n)
            st.dataframe(preview_table(pend["records"]), width="stretch", height=300)
            if not year2:
                st.info("Type the year to continue.")
            b1, b2, _ = st.columns([1, 1, 4])
            if b1.button("✅ Save", type="primary", key="save2", disabled=not year2):
                # each file is its own source
                by_src = {}
                for r in pend["records"]:
                    by_src.setdefault(r.pop("_src", None) or pend["source"], []).append(r)
                total = []
                ok = True
                for src, recs in by_src.items():
                    try:
                        line, stt, url = pipeline.save_records(client, sname, year2, recs, src)
                        total.append(f"{src[:40]}: {line}")
                    except (ClientError, ValueError) as e:
                        st.error(f"{src[:40]}: not saved ({e})")
                        ok = False
                        break
                if ok:
                    st.session_state["pending"] = None
                    st.session_state["done"] = {"line": " | ".join(total), "url": names[sname]["url"],
                                                "sheet": sname, "year": year2}
                    st.rerun()
            if b2.button("❌ Cancel", key="cancel2"):
                st.session_state["pending"] = None
                st.rerun()
