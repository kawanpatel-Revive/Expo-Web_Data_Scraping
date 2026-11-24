"""Matching companies and merging data into the rows of one year tab.

Rules (from the proposal):
  * existing values are never overwritten; only empty cells are filled
  * a different value for a one-value field goes to Extra, and Check says "Please check"
  * many-item columns (Product, Brochures ...) get new lines, never duplicates
  * contact persons go to the next free slot (max 4), the rest to Extra
  * the Hard Copy Brochure column is never touched
  * a company is found by website domain, email domain, phone, then name
"""
import re
from rapidfuzz import fuzz, process

from .columns import (HEADERS, IDX, SINGLE, MULTI, CONTACT_FIELDS, N_CONTACTS, contact_col,
                      MANUAL, empty_row)
from .util import (clean, norm_name, norm_url, norm_text, company_domain, phone_key, short_source,
                   EMAIL_RE, domain_of)

SOURCE = IDX["Source"]
CHECK = IDX["Check"]
EXTRA = IDX["Extra"]
UPDATED = IDX["Last Updated"]
NAME = IDX["Company Name"]
WEBSITE = IDX["Website"]
EMAIL = IDX["Email"]
FUZZY_MIN = 92          # similar names: merge only when websites do not disagree
NEAR_MIN = 88           # names inside one website group


def _stem(name):
    out = []
    for t in name.split():
        if t.endswith("ies") and len(t) > 4:
            t = t[:-3] + "y"
        elif t.endswith("s") and len(t) > 3 and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return " ".join(out)


def names_similar(a, b, cutoff=FUZZY_MIN):
    """Are two normalised company names the same company (typo, extra word like 'works')?

    Different numbers (Plant 1 / Plant 2) or different distinctive words never match.
    """
    if not a or not b:
        return False
    a, b = _stem(a), _stem(b)
    if a == b:
        return True
    da, db = set(re.findall(r"\d+", a)), set(re.findall(r"\d+", b))
    if da != db:
        return False
    ta, tb = set(a.split()), set(b.split())
    score = fuzz.token_sort_ratio(a, b)
    if ta <= tb or tb <= ta:
        return score >= cutoff - 6 if min(len(ta), len(tb)) >= 2 else score >= 99
    return score >= max(cutoff + 3, 95)


def new_record():
    """An empty record, the shape the readers produce."""
    r = {h: "" for h in SINGLE}
    for h in MULTI:
        r[h] = []
    r["Brochures"] = []          # still read from the pages, but there is no Brochures column in the Sheet
    r["contacts"] = []
    r["extra"] = []
    return r


def _lines(cell):
    return [x for x in (cell or "").split("\n") if x.strip()]


def _item_key(item):
    """Key to compare list items: the link when there is one, else the text."""
    item = clean(item)
    m = re.search(r"https?://\S+|www\.\S+", item, re.I)
    return norm_url(m.group(0)) if m else norm_text(item)


class TabState:
    """All rows of one tab in memory, with matching indexes and a list of what changed."""

    def __init__(self, rows, missing=()):
        self.missing = set(missing)
        self.rows = []
        for r in rows:
            r = list(r) + [""] * (len(HEADERS) - len(r))
            self.rows.append([clean_cell(c) for c in r[: len(HEADERS)]])
        self.flushed = len(self.rows)
        self.dirty = {}            # row index (< flushed) -> set of column indexes
        self.touched = set()       # rows touched in this run
        self.changed_rows = set()  # rows changed in this run
        self.new_rows = set()
        self.check_rows = set()
        self.skipped = 0
        self.merged = []           # (name in the list, name of the row it was joined to, why) - same run only
        self.dom, self.phone, self.name = {}, {}, {}
        for i in range(len(self.rows)):
            self._index(i)

    # ---------- indexes ----------
    def _index(self, i):
        r = self.rows[i]
        for d in self._domains(r):
            self.dom.setdefault(d, set()).add(i)
        for p in self._phones(r):
            self.phone.setdefault(p, set()).add(i)
        n = norm_name(r[NAME])
        if n:
            self.name.setdefault(n, set()).add(i)

    @staticmethod
    def _domains(r):
        out = set()
        d = company_domain(r[WEBSITE])
        if d:
            out.add(d)
        for cell in [r[EMAIL]] + [r[IDX[contact_col(n, "Email")]] for n in range(1, N_CONTACTS + 1)]:
            for e in EMAIL_RE.findall(cell or ""):
                d = company_domain(e)
                if d:
                    out.add(d)
        return out

    @staticmethod
    def _phones(r):
        out = set()
        cells = [r[IDX[contact_col(n, "Phone")]] for n in range(1, N_CONTACTS + 1)]
        for ln in _lines(r[EXTRA]):
            if ln.lower().startswith("phone:"):
                cells.append(ln[6:])
        for c in cells:
            for part in re.split(r"[;,/|]", c or ""):
                k = phone_key(part)
                if k:
                    out.add(k)
        return out

    # ---------- finding a company ----------
    def find(self, rec):
        """Return (row index or None, reason or '')."""
        name_n = norm_name(rec.get("Company Name", ""))
        rec_web = company_domain(rec.get("Website", ""))
        rec_doms = set()
        if rec_web:
            rec_doms.add(rec_web)
        for e in EMAIL_RE.findall(rec.get("Email", "") or ""):
            d = company_domain(e)
            if d:
                rec_doms.add(d)
        for c in rec.get("contacts", []):
            for e in EMAIL_RE.findall(c.get("Email", "") or ""):
                d = company_domain(e)
                if d:
                    rec_doms.add(d)

        # 1. website / email domain
        cand = set()
        for d in rec_doms:
            cand |= self.dom.get(d, set())
        if cand:
            if len(cand) == 1 and (not name_n or self._name_ok(next(iter(cand)), name_n, rec_web)):
                return next(iter(cand)), ""
            best = None
            for i in sorted(cand):
                if not name_n or names_similar(norm_name(self.rows[i][NAME]), name_n, NEAR_MIN):
                    best = i
                    break
            if best is not None:
                return best, ""
            if name_n:
                other = self.rows[next(iter(cand))][NAME]
                return None, f"same website as '{short_source(other, 40)}' (group company or duplicate?)"
            return next(iter(cand)), ""

        # 2. phone
        pk = set()
        for c in rec.get("contacts", []):
            for part in re.split(r"[;,/|]", c.get("Phone", "") or ""):
                k = phone_key(part)
                if k:
                    pk.add(k)
        for ln in rec.get("extra", []):
            if ln.lower().startswith("phone:"):
                for part in re.split(r"[;,/|]", ln[6:]):
                    k = phone_key(part)
                    if k:
                        pk.add(k)
        cand = set()
        for k in pk:
            cand |= self.phone.get(k, set())
        if len(cand) == 1:
            return next(iter(cand)), ""

        # 3. name
        if name_n:
            if name_n in self.name:
                i = sorted(self.name[name_n])[0]
                return i, ""
            names = list(self.name.keys())
            hits = process.extract(name_n, names, scorer=fuzz.token_sort_ratio, limit=5,
                                   score_cutoff=FUZZY_MIN - 8) if names else []
            for n, score, _ in hits:
                if names_similar(n, name_n):
                    best = sorted(self.name[n])[0]
                    row_web = company_domain(self.rows[best][WEBSITE])
                    if rec_web and row_web and rec_web != row_web:
                        return None, (f"name similar to '{short_source(self.rows[best][NAME], 40)}' "
                                      f"but website differs")
                    return best, ""
        return None, ""

    def _name_ok(self, i, name_n, rec_web):
        rn = norm_name(self.rows[i][NAME])
        if not rn or not name_n:
            return True
        return names_similar(rn, name_n, NEAR_MIN)

    # ---------- changing cells ----------
    def _set(self, i, col, value):
        if col in self.missing and col != EXTRA:
            # heading renamed or removed in the sheet: keep the data in Extra
            if col == UPDATED or not value:
                return False
            return self._add_line(i, EXTRA, f"{HEADERS[col]}: {value}")
        if self.rows[i][col] == value:
            return False
        self.rows[i][col] = value
        if i < self.flushed:
            self.dirty.setdefault(i, set()).add(col)
        return True

    def _add_line(self, i, col, line):
        line = clean(line)
        if not line:
            return False
        have = _lines(self.rows[i][col])
        key = _item_key(line) if col not in (EXTRA,) else norm_text(line)
        for h in have:
            hk = _item_key(h) if col not in (EXTRA,) else norm_text(h)
            if hk == key:
                return False
        return self._set(i, col, "\n".join(have + [line]))

    def _add_check(self, i, reason):
        cur = self.rows[i][CHECK]
        if reason in cur:
            return
        self._set(i, CHECK, ("Please check: " + reason) if not cur else cur + "; " + reason)
        self.check_rows.add(i)

    # ---------- merging ----------
    def merge(self, rec, source, now):
        """Merge one record. Returns 'new', 'updated', 'unchanged' or 'skipped'."""
        if not clean(rec.get("Company Name", "")):
            self.skipped += 1
            return "skipped"
        source = clean(source)
        i, reason = self.find(rec)
        created = False
        if i is not None and i in self.touched:          # a second record for a row already filled in this run
            w1, w2 = company_domain(rec.get("Website", "")), company_domain(self.rows[i][WEBSITE])
            self.merged.append((clean(rec.get("Company Name", "")), self.rows[i][NAME],
                                "same website" if w1 and w1 == w2 else "same / similar name"))
        if i is None:
            self.rows.append(empty_row())
            i = len(self.rows) - 1
            self.new_rows.add(i)
            created = True
        before = list(self.rows[i])
        src_short = short_source(source)
        row = self.rows[i]
        if created and reason:
            self._add_check(i, reason)

        for field in SINGLE:                      # one-value fields
            val = clean(rec.get(field, ""))
            if not val:
                continue
            col = IDX[field]
            cur = row[col]
            if not cur:
                self._set(i, col, val)
            elif self._same(field, cur, val):
                continue
            else:
                self._add_line(i, EXTRA, f"{field} (new): {val} (source: {src_short})")
                self._add_check(i, f"{field} differs, see Extra")
        for field in MULTI:                       # many-item fields
            for item in rec.get(field, []) or []:
                self._add_line(i, IDX[field], item)
        for c in rec.get("contacts", []) or []:   # contacts
            self._merge_contact(i, c)
        for ln in rec.get("extra", []) or []:     # anything else
            self._add_line(i, EXTRA, ln)
        self._add_line(i, SOURCE, source)

        self.touched.add(i)
        self._index(i)
        if created:
            self._set(i, UPDATED, now)
            return "new"
        if self.rows[i] != before:
            self.changed_rows.add(i)
            self._set(i, UPDATED, now)
            return "updated"
        return "unchanged"

    @staticmethod
    def _same(field, a, b):
        if field == "Website":
            return norm_url(a) == norm_url(b) or company_domain(a) == company_domain(b) and company_domain(a) != "" and \
                norm_url(a).split("/")[0] == norm_url(b).split("/")[0]
        if field == "Company Name":
            return norm_name(a) == norm_name(b)
        if field == "Email":
            return norm_text(a) == norm_text(b)
        return norm_text(a) == norm_text(b)

    def _merge_contact(self, i, c):
        c = {k: clean(c.get(k, "")) for k in CONTACT_FIELDS}
        if not any(c.values()):
            return
        slots = []
        for n in range(1, N_CONTACTS + 1):
            slots.append({f: self.rows[i][IDX[contact_col(n, f)]] for f in CONTACT_FIELDS})
        # same person already there?
        for n, s in enumerate(slots, start=1):
            same = (c["Email"] and s["Email"] and norm_text(c["Email"]) == norm_text(s["Email"])) or \
                   (phone_key(c["Phone"]) and phone_key(c["Phone"]) == phone_key(s["Phone"])) or \
                   (c["Name"] and s["Name"] and norm_text(c["Name"]) == norm_text(s["Name"]))
            if same:
                for f in CONTACT_FIELDS:
                    if c[f] and not s[f]:
                        self._set(i, IDX[contact_col(n, f)], c[f])
                return
        for n, s in enumerate(slots, start=1):
            if not any(s.values()):
                for f in CONTACT_FIELDS:
                    if c[f]:
                        self._set(i, IDX[contact_col(n, f)], c[f])
                return
        txt = " | ".join(x for x in [c["Name"], c["Designation"], c["Phone"], c["Email"]] if x)
        self._add_line(i, EXTRA, f"Contact (more than 4 persons): {txt}")

    # ---------- results ----------
    def flush(self):
        """What must be written to the sheet since the last flush.

        Returns (edits, appends, start_row):
          edits   = [[sheet_row, col_index, value], ...]   (sheet_row is 1-based, row 1 = headings)
          appends = [[35 values], ...]  to be written from start_row
        """
        edits = []
        for i in sorted(self.dirty):
            for col in sorted(self.dirty[i]):
                edits.append([i + 2, col, self.rows[i][col]])
        appends = [list(self.rows[i]) for i in range(self.flushed, len(self.rows))]
        start = self.flushed + 2
        self.flushed = len(self.rows)
        self.dirty = {}
        return edits, appends, start

    def stats(self):
        touched = self.touched
        new = len(self.new_rows & touched)
        updated = len((self.changed_rows - self.new_rows) & touched)
        unchanged = len(touched) - new - updated
        return {
            "found": len(touched),
            "new": new,
            "updated": updated,
            "unchanged": max(unchanged, 0),
            "check": len(self.check_rows & touched),
            "skipped": self.skipped,
            "merged": len(self.merged),
            "merged_list": self.merged[:200],
        }


def clean_cell(c):
    if c is None:
        return ""
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c).replace("\r\n", "\n").strip()


def summary_line(st):
    s = f"{st['found']} companies found: {st['new']} new, {st['updated']} updated"
    if st.get("unchanged"):
        s += f", {st['unchanged']} unchanged"
    s += "."
    if st.get("merged"):
        s += f" {st['merged']} listed companies were joined with another one (same website or name)."
    if st["check"]:
        s += f" {st['check']} need a check (see Check column)."
    return s
