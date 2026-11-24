"""Reads files (PDF, image/photo/card, Excel/CSV) and Google Sheet / Drive data into company records.
Rules only, no AI. Email, phone and website are reliable; names/designations from photos can be wrong."""
import io, re
from .matcher import new_record
from .util import clean, EMAIL_RE, fix_url, root_domain, domain_of, SOCIAL, FREE_MAIL
from . import extract as ex

PHONE_FIND = re.compile(r"(?<![\w/])(\+?\d[\d\s().\-]{7,17}\d)(?![\w])")
URL_FIND = re.compile(r"(?:https?://|www\.)[^\s,;<>\"']+", re.I)
BARE_DOMAIN = re.compile(r"\b(?:[a-z0-9\-]+\.)+(?:com|in|net|org|co|de|it|fr|uk|io|biz|info|eu|cn|jp|ae|sg)(?:/\S*)?\b", re.I)
DESIG = re.compile(r"\b(director|manager|ceo|cto|cfo|coo|md|managing|president|founder|owner|head|chief|"
                   r"executive|engineer|sales|marketing|partner|proprietor|general|officer|vp|vice|"
                   r"consultant|specialist|representative|chairman|export|business development)\b", re.I)
LEGAL = re.compile(r"\b(pvt|ltd|limited|llp|inc|gmbh|corp|corporation|co\.|company|industries|enterprises|"
                   r"engineering|technologies|systems|solutions|exports?|traders|manufacturers?|works|group|"
                   r"machines|tools|pumps|steel|aluminium|aluminum|trading)\b", re.I)
ADDR = re.compile(r"\b(road|rd\.?|street|st\.|plot|sector|nagar|industrial|estate|phase|floor|building|"
                  r"pin|pincode|gujarat|india|mumbai|delhi|ahmedabad|rajkot|surat|pune|chennai|"
                  r"germany|italy|china|district|park|area|gidc|midc)\b", re.I)


# ------------------------------------------------------------ text -> record(s)
def _site_ok(u):
    d = root_domain(domain_of(u) or "")
    return d and d not in SOCIAL and d not in FREE_MAIL


def card_from_text(text):
    """One visiting card (OCR text) -> one record."""
    rec = new_record()
    lines = [clean(l) for l in text.splitlines() if clean(l)]
    emails = list(dict.fromkeys(EMAIL_RE.findall(text)))
    if emails:
        rec["Email"] = emails[0]
        for e in emails[1:3]:
            rec["extra"].append("Email: " + e)
    for u in URL_FIND.findall(text):
        u = u.rstrip(".,)")
        if _site_ok(u) and not rec["Website"]:
            rec["Website"] = fix_url(u)
    if not rec["Website"]:
        for d in BARE_DOMAIN.findall(text):
            if "@" in text[max(0, text.find(d) - 1):text.find(d)] or d.lower() in " ".join(emails).lower():
                continue
            if _site_ok(d):
                rec["Website"] = fix_url(d); break
    if not rec["Website"] and emails:
        dom = emails[0].split("@")[1]
        if _site_ok(dom):
            rec["Website"] = "https://www." + dom.lower() if not dom.lower().startswith("www.") else "https://" + dom
    phones = []
    for m in PHONE_FIND.finditer(text):
        p = clean(m.group(1))
        if len(re.sub(r"\D", "", p)) >= 8 and p not in phones:
            phones.append(p)
    contact = {"Name": "", "Designation": "", "Phone": phones[0] if phones else "", "Email": rec["Email"]}
    rest = []
    for l in lines:
        if EMAIL_RE.search(l) or URL_FIND.search(l) or PHONE_FIND.search(l) and len(re.sub(r"[\D]", "", l)) >= 8:
            continue
        l = re.sub(r"^(mob|mobile|tel|phone|email|e-mail|web|website|ph|m|t|e|w)\s*[:.\-]\s*", "", l, flags=re.I)
        if len(l) < 3 or not re.search(r"[A-Za-z]", l):
            continue
        rest.append(l)
    comp = next((l for l in rest if LEGAL.search(l) and not DESIG.search(l)), "")
    desig = next((l for l in rest if DESIG.search(l) and l != comp and len(l) <= 60), "")
    addr_lines = [l for l in rest if ADDR.search(l) or re.search(r"\d{5,6}", l)]
    name = ""
    for l in rest:
        if l in (comp, desig) or l in addr_lines:
            continue
        if re.fullmatch(r"[A-Za-z.\- ]{3,40}", l) and 1 <= len(l.split()) <= 4:
            name = l; break
    if not comp and rest:
        comp = next((l for l in rest if l not in (desig, name) and l not in addr_lines), "")
    rec["Company Name"] = comp
    contact["Name"], contact["Designation"] = name, desig
    if contact["Name"] or contact["Phone"] or contact["Designation"]:
        rec["contacts"].append(contact)
    if addr_lines:
        rec["Address / Corporate Address"] = ", ".join(addr_lines)
    for p in phones[1:3]:
        rec["extra"].append("Phone: " + p)
    return rec


def block_records_from_text(text):
    """Brochure / flyer / pamphlet text -> usually ONE company: name from first strong line."""
    rec = card_from_text(text)
    paras = [clean(p) for p in re.split(r"\n\s*\n", text) if len(clean(p)) > 80]
    if paras and not rec["About the Company"]:
        rec["About the Company"] = paras[0][:1500]
    return rec


# ------------------------------------------------------------ PDF
def pdf_text_pages(data):
    import pdfplumber
    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    return pages


def ocr_image(img):
    import os, pytesseract
    for exe in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(exe):
            pytesseract.pytesseract.tesseract_cmd = exe
            break
    return pytesseract.image_to_string(img)


def pdf_ocr_pages(data, dpi=200, limit=60):
    import pymupdf
    from PIL import Image
    doc = pymupdf.open(stream=data, filetype="pdf")
    out = []
    for i, page in enumerate(doc):
        if i >= limit:
            break
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        out.append(ocr_image(img))
    return out


def read_pdf(data, progress=None, fixed_company=""):
    """PDF with a company table -> several records; a brochure -> one record."""
    notes = []
    pages = pdf_text_pages(data)
    if sum(len(p.strip()) for p in pages) < 50 * max(1, len(pages)) * 0.2:
        notes.append("This PDF has no real text; reading by OCR (slower, may have mistakes).")
        pages = pdf_ocr_pages(data)
    text = "\n".join(pages)
    recs = table_records_from_pdf(data) if _looks_tabular(pages) else []
    if len(recs) >= 3:
        return recs, notes
    return [block_records_from_text(text)], notes


def _looks_tabular(pages):
    t = "\n".join(pages).lower()
    return sum(1 for k in ("company", "exhibitor", "email", "website", "country") if k in t) >= 2


def table_records_from_pdf(data):
    import pdfplumber
    recs = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        head = None
        for p in pdf.pages:
            for tb in p.extract_tables():
                for row in tb:
                    cells = [clean(c or "") for c in row]
                    tg = [ex.label_target(c) for c in cells]
                    if "Company Name" in tg:
                        head = cells; continue
                    if head is None or len(cells) != len(head) or not any(cells):
                        continue
                    recs.append(_record_from_row(head, cells))
    return [r for r in recs if r["Company Name"]]


# ------------------------------------------------------------ Excel / CSV
def _record_from_row(headers, values):
    rec = new_record()
    cur = None
    pairs = []
    for h, v in zip(headers, values):
        v = clean(str(v)) if v is not None else ""
        if not v or v.lower() == "nan":
            continue
        t = ex.label_target(h)
        if t is None:
            if clean(h):
                rec["extra"].append(f"{clean(h)}: {v}")
            continue
        pairs.append((t, h, v))
    ex.fill_from_pairs(rec, pairs)
    return rec


def read_table_file(data, filename):
    import pandas as pd
    notes, recs = [], []
    name = filename.lower()
    if name.endswith((".csv", ".txt")):
        try:
            frames = {"CSV": pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)}
        except Exception:
            frames = {"CSV": pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="latin-1")}
    else:
        frames = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str, header=None, keep_default_na=False)
    for sh, df in frames.items():
        rows = df.values.tolist() if name.endswith((".csv", ".txt")) is False else [list(df.columns)] + df.values.tolist()
        recs += records_from_rows(rows)
    return recs, notes


def records_from_rows(rows):
    """rows = list of lists, first rows may be titles; find the heading row (has a Company Name label)."""
    hi = None
    for i, r in enumerate(rows[:15]):
        if any(ex.label_target(str(c)) == "Company Name" for c in r):
            hi = i; break
    if hi is None:
        return []
    heads = [clean(str(c)) for c in rows[hi]]
    out = []
    for r in rows[hi + 1:]:
        rec = _record_from_row(heads, list(r))
        if rec["Company Name"]:
            out.append(rec)
    return out


# ------------------------------------------------------------ images (photo, screenshot, card)
def read_image(data):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
    if img.width < 1400:
        k = 1400 / img.width
        img = img.resize((int(img.width * k), int(img.height * k)))
    text = ocr_image(img)
    if not clean(text):
        return [], ["No text could be read from this image."]
    return [block_records_from_text(text)], ["Read by OCR: please check names and designations."]


# ------------------------------------------------------------ entry
def read_upload(name, data):
    low = name.lower()
    if low.endswith(".pdf"):
        return read_pdf(data)
    if low.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        return read_table_file(data, name)
    if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
        return read_image(data)
    raise ValueError("This file type is not supported. Use PDF, image, Excel or CSV.")


def read_google_sheet(client, link):
    recs = []
    for sh in client.read_external(link):
        recs += records_from_rows(sh["values"])
    return recs, []


def read_drive_file(client, link):
    name, data = client.get_file(link)
    return read_upload(name, data)


# ------------------------------------------------------------ pasted text (copy from a web page)
def records_from_pasted(text):
    """One company per line (numbers/bullets removed). Tab-separated lines (copied from a table):
    first part = name; email / web address / country-like parts are recognised."""
    recs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [clean(x) for x in line.split("\t") if clean(x)]
        name = ex.BULLET.sub("", parts[0]) if parts else ""
        if parts and re.fullmatch(r"\d{1,4}\.?", parts[0]) and len(parts) > 1:
            parts = parts[1:]
            name = parts[0]
        if not name or name.lower() in ex.NAV_WORDS or len(name) > 150 or ex.looks_like_domain(name):
            continue
        rec = new_record()
        rec["Company Name"] = name
        for x in parts[1:]:
            if EMAIL_RE.fullmatch(x):
                rec["Email"] = x
            elif ex.looks_like_domain(x):
                rec["Website"] = fix_url(x)
            else:
                rec["extra"].append(x)
        recs.append(rec)
    return ex.dedupe_names(recs)
