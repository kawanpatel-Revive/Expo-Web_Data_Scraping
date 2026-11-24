"""Smoke test of the screen with a pretend Google (FakeClient)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from streamlit.testing.v1 import AppTest
from expo.client import FakeClient
from expo.extract import *  # noqa
from expo.matcher import new_record

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
fc = FakeClient()

def fresh():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["client_override"] = fc
    return at

at = fresh().run()
assert not at.exception, at.exception
# pending preview -> Save creates Sheet + tab
recs = []
for n in ("Alpha Pumps", "Beta Valves"):
    r = new_record(); r["Company Name"] = n; r["Email"] = f"x@{n.split()[0].lower()}.com"; recs.append(r)
at.session_state["pending"] = {"kind": "web", "records": recs, "site": "Smart Expo", "year": "2026", "notes": [], "source": "https://smart.example/list"}
at.run()
assert not at.exception, at.exception
save = [b for b in at.button if b.key == "save1"][0]
save.click().run()
assert not at.exception, at.exception
assert len(fc.sheets) == 1, fc.sheets
sheet = list(fc.sheets.values())[0]
assert sheet["name"] == "Smart Expo" and "2026" in sheet["tabs"] and len(sheet["tabs"]["2026"]) == 3
assert any("2 companies found: 2 new" in s.value for s in at.success), [s.value for s in at.success]
# same list again: no duplicates
at.session_state["pending"] = {"kind": "web", "records": recs, "site": "Smart Expo", "year": "2026", "notes": [], "source": "https://smart.example/list"}
at.run(); [b for b in at.button if b.key == "save1"][0].click().run()
assert len(sheet["tabs"]["2026"]) == 3
assert any("2 unchanged" in s.value or "0 new" in s.value for s in at.success), [s.value for s in at.success]
print("app smoke test passed")
