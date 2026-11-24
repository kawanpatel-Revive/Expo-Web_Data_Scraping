import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from expo.columns import HEADERS, IDX, empty_row
from expo.matcher import TabState, new_record, summary_line
from expo.client import FakeClient
from expo.tabio import build_state, save_state

NOW = "2026-11-03 10:00"
SRC = "https://expo.example/list"


def rec(name, **kw):
    r = new_record()
    r["Company Name"] = name
    for k, v in kw.items():
        k = k.replace("_", " ")
        r[k] = v
    return r


def cell(st, i, h):
    return st.rows[i][IDX[h]]


def test_new_list_and_repeat():
    st = TabState([])
    for n, w in [("ABC Industries Pvt Ltd", "www.abc.com"), ("XYZ Pumps", "https://xyz.de"), ("Foo", "")]:
        assert st.merge(rec(n, Website=w), SRC, NOW) == "new"
    s = st.stats()
    assert s["found"] == 3 and s["new"] == 3 and s["updated"] == 0
    edits, apps, start = st.flush()
    assert len(apps) == 3 and start == 2 and not edits
    # same list again, in a fresh run on the saved rows
    st2 = TabState(apps)
    for n, w in [("ABC Industries", "www.abc.com"), ("XYZ Pumps", "xyz.de"), ("Foo", "")]:
        assert st2.merge(rec(n, Website=w), SRC, NOW) == "unchanged", n
    e2, a2, _ = st2.flush()
    assert not e2 and not a2
    assert st2.stats()["unchanged"] == 3 and st2.stats()["new"] == 0


def test_new_data_added_no_duplicates():
    st = TabState([])
    st.merge(rec("ABC", Website="www.abc.com"), SRC, NOW)
    base = st.flush()[1]
    st = TabState(base)
    r = rec("ABC", Website="abc.com", Product=["Pumps", "Valves"], Brochures=["All Brands: https://x.com/a.pdf"])
    r["Country"] = "India"
    assert st.merge(r, "list2", NOW) == "updated"
    assert cell(st, 0, "Product") == "Pumps\nValves"
    assert cell(st, 0, "Country") == "India"
    edits, apps, _ = st.flush()
    assert apps == [] and len(edits) >= 3
    rows = [list(r) for r in st.rows]
    st = TabState(rows)
    r2 = rec("ABC", Product=["valves", "Seals"], Brochures=["Brand book: https://www.x.com/a.pdf", "Series A: https://x.com/b.pdf"])
    st.merge(r2, "list3", NOW)
    assert cell(st, 0, "Product") == "Pumps\nValves\nSeals"
    assert cell(st, 0, "Brochures").count("\n") == 1  # same link not added twice
    assert "list2" in cell(st, 0, "Source") and "list3" in cell(st, 0, "Source")


def test_conflict_goes_to_extra():
    st = TabState([])
    st.merge(rec("ABC Industries", Website="www.abc.com", Email="info@abc.com"), SRC, NOW)
    st = TabState(st.flush()[1])
    # brochure: same email domain, different website
    r = rec("ABC Industries", Website="www.abc-industries.com", Email="info@abc.com")
    assert st.merge(r, "brochure.pdf", NOW) == "updated"
    assert cell(st, 0, "Website") == "www.abc.com"
    assert "Website (new): www.abc-industries.com (source: brochure.pdf)" in cell(st, 0, "Extra")
    assert cell(st, 0, "Check") == "Please check: Website differs, see Extra"
    assert st.stats()["check"] == 1


def test_same_company_different_booths_merge():
    st = TabState([])
    a = rec("ABC Industries", Website="www.abc.com"); a["extra"] = ["Booth: A-12"]
    b = rec("ABC Industries Ltd", Website="https://abc.com/"); b["extra"] = ["Booth: B-07"]
    assert st.merge(a, SRC, NOW) == "new"
    assert st.merge(b, SRC, NOW) == "updated"
    assert len(st.rows) == 1
    assert cell(st, 0, "Extra") == "Booth: A-12\nBooth: B-07"
    assert st.stats()["found"] == 1 and st.stats()["new"] == 1


def test_group_companies_same_website_not_merged():
    st = TabState([])
    st.merge(rec("Tata Power", Website="www.tata.com"), SRC, NOW)
    st.merge(rec("Tata Steel", Website="www.tata.com"), SRC, NOW)
    assert len(st.rows) == 2
    assert "same website" in cell(st, 1, "Check")
    assert st.stats()["check"] == 1


def test_similar_name_different_website():
    st = TabState([])
    st.merge(rec("Shree Ram Engineering", Website="www.shreeram.com"), SRC, NOW)
    st.merge(rec("Shree Ram Engineering Works", Website="www.sre-works.in"), SRC, NOW)
    # names not >=92 similar after normalising? either way a different website must never merge silently
    if len(st.rows) == 1:
        assert "differs" in cell(st, 0, "Check")
    else:
        assert cell(st, 1, "Website") == "www.sre-works.in"


def test_contacts_slots():
    st = TabState([])
    r = rec("ABC", Website="abc.com")
    r["contacts"] = [{"Name": f"Person {i}", "Phone": f"98765432{i}0", "Email": f"p{i}@abc.com", "Designation": "Mgr"} for i in range(1, 6)]
    st.merge(r, SRC, NOW)
    assert cell(st, 0, "Contact Person 4 Email") == "p4@abc.com"
    assert "Contact (more than 4 persons): Person 5" in cell(st, 0, "Extra")
    st = TabState(st.flush()[1])
    r2 = rec("ABC", Website="abc.com")
    r2["contacts"] = [{"Name": "P1", "Email": "P1@abc.com", "Designation": "Director"}]
    st.merge(r2, "card.jpg", NOW)
    assert cell(st, 0, "Contact Person 1 Name") == "Person 1"          # not overwritten
    assert cell(st, 0, "Contact Person 1 Designation") == "Mgr"
    assert cell(st, 0, "Contact Person 2 Name") == "Person 2"


def test_hard_copy_and_manual_edits_stay():
    row = empty_row()
    row[IDX["Company Name"]] = "ABC"; row[IDX["Website"]] = "abc.com"
    row[IDX["Hard Copy Brochure"]] = "Yes"
    row[IDX["About the Company"]] = "my own words"
    st = TabState([row])
    r = rec("ABC", Website="abc.com"); r["About the Company"] = "From the expo"
    st.merge(r, SRC, NOW)
    assert cell(st, 0, "Hard Copy Brochure") == "Yes"
    assert cell(st, 0, "About the Company") == "my own words"
    assert "About the Company (new)" in cell(st, 0, "Extra")
    # cleared cell is refilled
    row2 = list(st.rows[0]); row2[IDX["About the Company"]] = ""
    st = TabState([row2]); st.merge(r, SRC, NOW)
    assert cell(st, 0, "About the Company") == "From the expo"


def test_phone_and_name_match():
    row = empty_row()
    row[IDX["Company Name"]] = "Acme Tools"
    row[IDX["Contact Person 1 Phone"]] = "+91 98765 43210"
    st = TabState([row])
    r = rec("Totally Other Name"); r["contacts"] = [{"Phone": "098765-43210"}]
    st.merge(r, "card", NOW)
    assert len(st.rows) == 1
    st2 = TabState([row]); st2.merge(rec("ACME TOOLS PVT. LTD."), "x", NOW)
    assert len(st2.rows) == 1


def test_name_rules():
    from expo.matcher import names_similar
    from expo.util import norm_name as n
    assert names_similar(n("ABC Industries"), n("ABC Industry"))
    assert names_similar(n("Shree Ram Engineering"), n("Shree Ram Engineering Works"))
    assert not names_similar(n("Alpha Tools"), n("Alpha Toys"))
    assert not names_similar(n("Plant 1 Steel"), n("Plant 2 Steel"))
    assert not names_similar(n("Tata Power"), n("Tata Steel"))
    st = TabState([])
    for k in range(50):
        st.merge(rec(f"Company Number {k} Engineering Works"), SRC, NOW)
    assert len(st.rows) == 50


def test_unknown_and_skipped():
    st = TabState([])
    assert st.merge(rec(""), SRC, NOW) == "skipped"
    assert st.stats()["skipped"] == 1


def test_renamed_heading_goes_to_extra():
    c = FakeClient()
    sid = c.ensure_sheet("S")["id"]; c.ensure_tab(sid, "2026")
    c.sheets[sid]["tabs"]["2026"][0][IDX["Country"]] = "Nation"   # user renamed it
    header, rows = c.read_tab(sid, "2026")
    st, cm, w = build_state(header, rows)
    r = rec("ABC", Website="abc.com"); r["Country"] = "India"
    st.merge(r, SRC, NOW)
    save_state(c, sid, "2026", st, cm, w)
    _, rows = c.read_tab(sid, "2026")
    assert rows[0][IDX["Extra"]] == "Country: India"
    assert rows[0][IDX["Nation"] if "Nation" in IDX else IDX["Country"]] == ""


def test_end_to_end_with_fake_client_batches():
    c = FakeClient()
    sid = c.ensure_sheet("India Energy Week")["id"]; c.ensure_tab(sid, "2026")
    header, rows = c.read_tab(sid, "2026")
    st, cm, w = build_state(header, rows)
    for k in range(400):
        st.merge(rec(f"Company {k} Ltd", Website=f"www.c{k}.com"), SRC, NOW)
    save_state(c, sid, "2026", st, cm, w)
    _, rows = c.read_tab(sid, "2026")
    assert len(rows) == 400
    # second run, 50 new + all old + new data for 10
    header, rows = c.read_tab(sid, "2026")
    st, cm, w = build_state(header, rows)
    for k in range(450):
        r = rec(f"Company {k} Ltd", Website=f"www.c{k}.com")
        if k < 10:
            r["Product"] = ["Widgets"]
        st.merge(r, SRC, NOW)
    s = st.stats()
    assert (s["found"], s["new"], s["updated"], s["unchanged"]) == (450, 50, 10, 390), s
    save_state(c, sid, "2026", st, cm, w)
    _, rows = c.read_tab(sid, "2026")
    assert len(rows) == 450 and rows[3][IDX["Product"]] == "Widgets"
    assert summary_line(s).startswith("450 companies found: 50 new, 10 updated, 390 unchanged.")


if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); n += 1; print("ok", k)
    print(n, "tests passed")
