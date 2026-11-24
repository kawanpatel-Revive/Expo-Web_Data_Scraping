"""Talks to the small Google Apps Script (real) or to an in-memory copy (for tests)."""
import json
import time
import uuid
import requests

from .columns import HEADERS, MANUAL


class ClientError(Exception):
    pass


class AppsScriptClient:
    """Sends requests to the Apps Script web app. Every request carries the secret password."""

    def __init__(self, url, secret, timeout=180):
        self.url = url.strip()
        self.secret = secret
        self.timeout = timeout

    def _call(self, action, **payload):
        body = json.dumps({"secret": self.secret, "action": action, **payload})
        last = None
        for attempt in range(3):
            try:
                r = requests.post(self.url, data=body.encode("utf-8"),
                                  headers={"Content-Type": "text/plain;charset=utf-8"},
                                  timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as e:
                last = f"No connection to Google ({e.__class__.__name__}). Check the internet."
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 500:
                last = f"Google returned error {r.status_code}."
                time.sleep(2 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                raise ClientError(
                    "The Apps Script link did not give a proper answer. Check that it is deployed as a "
                    "Web app (Execute as: Me, Who has access: Anyone) and that the URL ends with /exec.")
            if not data.get("ok"):
                raise ClientError(data.get("error", "Unknown error from the Apps Script."))
            return data
        raise ClientError(last or "Could not reach Google.")

    def ping(self):
        return self._call("ping")

    def list_sheets(self):
        return self._call("list_sheets")["sheets"]

    def ensure_sheet(self, name):
        return self._call("ensure_sheet", name=name)

    def list_tabs(self, sheet_id):
        return self._call("list_tabs", sheetId=sheet_id)["tabs"]

    def ensure_tab(self, sheet_id, tab):
        return self._call("ensure_tab", sheetId=sheet_id, tab=tab, headers=HEADERS, manual=MANUAL)

    def read_tab(self, sheet_id, tab):
        d = self._call("read_tab", sheetId=sheet_id, tab=tab)
        return d["header"], d["rows"]

    def write_tab(self, sheet_id, tab, edits, appends, start_row):
        self._call("write_tab", sheetId=sheet_id, tab=tab, edits=edits, appends=appends, startRow=start_row)

    def read_external(self, url):
        return self._call("read_external", url=url)["sheets"]  # [{"name","values"}]

    def get_file(self, url):
        import base64
        d = self._call("get_file", url=url)
        return d["name"], base64.b64decode(d["b64"])


class FakeClient:
    """Same interface, everything kept in memory. Used by the tests."""

    def __init__(self):
        self.sheets = {}  # id -> {"name":..., "tabs": {tab: [header, row, row...]}}
        self.external = {}  # url -> [{"name","values"}]
        self.files = {}     # url -> {"name","mime","b64"}
        self.calls = []

    def ping(self):
        return {"ok": True}

    def list_sheets(self):
        return [{"id": k, "name": v["name"], "url": f"https://docs.google.com/spreadsheets/d/{k}"}
                for k, v in self.sheets.items()]

    def ensure_sheet(self, name):
        for k, v in self.sheets.items():
            if v["name"] == name:
                return {"id": k, "url": f"https://docs.google.com/spreadsheets/d/{k}", "created": False}
        k = "fake" + uuid.uuid4().hex[:8]
        self.sheets[k] = {"name": name, "tabs": {}}
        return {"id": k, "url": f"https://docs.google.com/spreadsheets/d/{k}", "created": True}

    def list_tabs(self, sheet_id):
        return list(self.sheets[sheet_id]["tabs"])

    def ensure_tab(self, sheet_id, tab):
        tabs = self.sheets[sheet_id]["tabs"]
        if tab in tabs:
            return {"created": False}
        tabs[tab] = [list(HEADERS)]
        return {"created": True}

    def read_tab(self, sheet_id, tab):
        t = self.sheets[sheet_id]["tabs"][tab]
        return list(t[0]), [list(r) for r in t[1:]]

    def write_tab(self, sheet_id, tab, edits, appends, start_row):
        self.calls.append(("write", len(edits), len(appends)))
        t = self.sheets[sheet_id]["tabs"][tab]
        for row, col, val in edits:
            while len(t) < row:
                t.append([""] * len(t[0]))
            r = t[row - 1]
            while len(r) <= col:
                r.append("")
            r[col] = val
        for k, row in enumerate(appends):
            idx = start_row - 1 + k
            while len(t) <= idx:
                t.append([""] * len(t[0]))
            t[idx] = list(row)

    def read_external(self, url):
        if url not in self.external:
            raise ClientError("No access to that Google Sheet.")
        return self.external[url]

    def get_file(self, url):
        if url not in self.files:
            raise ClientError("No access to that Drive file.")
        import base64
        f = self.files[url]
        return f["name"], base64.b64decode(f["b64"])
