"""Reading expo pages by rules (no AI): tables, repeated cards, labelled text, links.

The same rules work on many sites, because they look for known labels (Website, Country,
Booth, Brochure ...), links to PDF files, and patterns for emails and phone numbers.
"""
import re
from urllib.parse import unquote, urljoin, urlparse
from bs4 import BeautifulSoup, Comment, Tag, NavigableString

from .matcher import new_record
from .columns import SINGLE, MULTI as _SHEET_MULTI

MULTI = list(_SHEET_MULTI) + ["Brochures"]      # brochure links are still read, but the Sheet has no such column
from .util import (norm_name, clean, clean_multiline, EMAIL_RE, company_domain, root_domain, domain_of, fix_url,
                   SOCIAL, norm_url)

CONTACT_NAME, CONTACT_DESIG, CONTACT_PHONE, COMPANY_PHONE = "@cname", "@cdesig", "@cphone", "@phone"
IGNORE = "@ignore"

LABEL_MAP = {}


def _lm(target, *labels):
    for l in labels:
        LABEL_MAP[l] = target


_lm("Company Name", "company name", "company", "exhibitor", "exhibitor name", "name of exhibitor",
    "name of the exhibitor", "organisation", "organization", "participant", "firm name", "firm",
    "name of company", "name of the company", "exhibitor company", "company/exhibitor")
_lm("Country", "country", "nation", "country name")
_lm("Website", "website", "web site", "web", "url", "homepage", "home page", "company website",
    "web address", "website url", "site", "www")
_lm("Address / Corporate Address", "address", "corporate address", "registered office",
    "office address", "postal address", "company address", "head office", "registered address",
    "full address", "contact address")
_lm("About the Company", "about", "about us", "about the company", "description", "overview",
    "company description", "about company", "who we are", "about the exhibitor")
_lm("Company Profile", "profile", "company profile", "short company profile", "short profile",
    "short description", "company overview", "exhibitor profile")
_lm("Email", "email", "e-mail", "email id", "e mail", "mail", "email address", "company email")
_lm("Product", "product", "products", "product details", "products & services", "products and services",
    "product range", "product description", "products offered", "products displayed", "exhibits",
    "product profile", "products/services", "product & services",
    "product category", "product categories", "product groups", "product group")
_lm("Category", "category", "categories", "main category", "suppliers of", "supplier of", "supplies", "we supply")
_lm("Subcategory", "subcategory", "sub category", "sub categories", "subcategories", "sub-category",
    "product subcategory", "sub-categories")
_lm("Services", "services", "service", "our services")
_lm("Brochures", "downloads", "documents", "literature", "brochure", "brochures", "catalogue", "catalog", "catalogues", "download brochure",
    "brochure download", "company brochure", "product brochure")
_lm("Press Release", "press release", "press releases", "press")
_lm("Video", "video", "videos", "company video", "video gallery")
_lm(CONTACT_NAME, "contact person", "contact name", "contact", "representative", "contact person name",
    "person", "key contact", "contact persons")
_lm(CONTACT_DESIG, "designation", "position", "job title", "role")
_lm(CONTACT_PHONE, "mobile", "mobile no", "mobile number", "cell")
_lm(COMPANY_PHONE, "phone", "tel", "telephone", "phone number", "contact number", "contact no",
    "phone no", "tel no", "telephone number", "landline")
_lm(IGNORE, "s no", "sr no", "sl no", "sno", "s.no", "no", "#", "sr", "sl", "serial no", "sr. no", "sl. no")
EXTRA_LABELS = {
    "booth", "booth no", "booth number", "booth no.", "stall", "stall no", "stall number", "stand",
    "stand no", "stand number", "hall", "hall no", "hall number", "hall no.", "hall/stand", "hall / stand",
    "social media", "linkedin", "facebook", "twitter", "instagram", "state", "city", "pincode",
    "pin code", "zip", "fax", "location", "sector", "industry", "area", "space", "pavilion",
}
MULTI_SPLIT = re.compile(r"\s*(?:\n|;|\||•|·)\s*")
COMMA_SPLIT = re.compile(r"\s*(?:\n|;|,|\||•|·)\s*")
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "select", "option", "iframe"}
NAV_TAGS = {"nav", "footer", "header", "aside"}
PLACEHOLDER_RE = re.compile(r"(placeholder|default[-_]|no[-_]?image|noimage|dummy|coming[-_]?soon|blank\.)", re.I)
GENERIC_LINK_TEXT = re.compile(r"^(view|download|click here|here|open|pdf|link|read more|more|details?|"
                               r"download pdf|view pdf|view / download|file|attachment)\b", re.I)


def norm_label(s):
    s = clean(s).lower().rstrip(":：-–").strip()
    s = re.sub(r"\s+", " ", s)
    return s


COMPANY_HEAD = re.compile(
    r"^(?:(?:name|list) of (?:the )?)?(?:exhibitor'?s?|exhibiting )?(?:exhibitors?|companies|company|participants?|firms?|"
    r"organi[sz]ations?|members?|brands?)(?: ?[/&] ?(?:exhibitors?|companies|company|organi[sz]ations?))?(?:'?s)?(?: name)?s?$|"
    r"^(?:exhibitor|company|participant|firm|organi[sz]ation|member|brand)'?s? name$|^name$", re.I)


def heading_target(label):
    """Table column heading -> Sheet column. Also accepts 'Name of Exhibitors', 'Participants', 'Name' ..."""
    t = label_target(label)
    if t is None and COMPANY_HEAD.match(norm_label(label)):
        return "Company Name"
    return t


def label_target(label):
    l = norm_label(label)
    if l in LABEL_MAP:
        return LABEL_MAP[l]
    if l in EXTRA_LABELS:
        return "@extra"
    return None


# ---------------------------------------------------------------- text of a block
def _is_hidden(el):
    st = (el.get("style") or "").replace(" ", "").lower()
    return "display:none" in st or el.get("hidden") is not None or el.get("aria-hidden") == "true" and False


END_MARK = "§END§"          # ends a value: the lines after it are not part of it


def _is_label_leaf(el):
    if not isinstance(el, Tag) or el.find(True):
        return False
    t = clean(el.get_text(" "))
    return bool(t) and len(t) <= 40 and bool(label_target(t))


def _caption_pairs(root):
    """Some sites print the VALUE first and a small caption (its label) UNDER it. A block whose children are
    value, caption, value, caption ... (first child is a value, last child is a caption) becomes
    'Label: value' lines, so it reads like every other page."""
    parents, seen = [], set()
    for cap in root.find_all(True):
        if _is_label_leaf(cap) and cap.parent is not None and id(cap.parent) not in seen:
            seen.add(id(cap.parent)); parents.append(cap.parent)
    for par in parents:
        if par.parent is None:
            continue
        kids = [c for c in par.children if (isinstance(c, Tag) and (clean(c.get_text(" ")) or c.name == "img")) or
                (isinstance(c, NavigableString) and clean(str(c)) and not isinstance(c, Comment))]
        if len(kids) < 2 or not _is_label_leaf(kids[-1]) or _is_label_leaf(kids[0]):
            continue
        lines, vals = [], []
        for c in kids:
            if _is_label_leaf(c):
                lab = clean(c.get_text(" "))
                v = "\n".join(x for x in vals if x)
                if v:
                    lines.append(f"{lab}: {v}")
                vals = []
            else:
                t = clean_multiline(c.get_text("\n")) if isinstance(c, Tag) else clean(str(c))
                vals.append(t)
        if vals and any(vals):                      # something after the last caption: not this layout
            continue
        if lines:
            par.replace_with(NavigableString("\n" + "\n".join(l + "\n" + END_MARK for l in lines) + "\n"))


def block_lines(el):
    """Text of an element as lines. A table row with two cells and a dt/dd pair become 'label: value'."""
    el = BeautifulSoup(str(el), "lxml")
    for t in el.find_all(list(SKIP_TAGS)):
        t.decompose()
    for t in el.find_all(["mat-icon", "button"]) + el.select(".material-icons, .material-icons-outlined, .mat-icon"):
        t.decompose()                       # icon names ("handshake") and button captions are not content
    _caption_pairs(el)
    for tr in el.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2 and not tr.find("table"):
            a, b = clean(cells[0].get_text(" ")), clean_multiline(cells[1].get_text("\n"))
            if a and label_target(a):
                tr.replace_with(NavigableString(f"\n{a}: {b}\n"))
    for dt in el.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            a, b = clean(dt.get_text(" ")), clean_multiline(dd.get_text("\n"))
            dt.replace_with(NavigableString(f"\n{a}: {b}\n"))
            dd.decompose()
    text = el.get_text("\n")
    lines = [clean(x) for x in text.split("\n")]
    return [x for x in lines if x]


def parse_lines(lines):
    """Pick 'label: value' pairs. Returns (pairs, free_lines); pairs = [(target, label, value)]."""
    pairs, free = [], []
    i, n = 0, len(lines)

    def is_label_line(s):
        m = re.match(r"^([^:：]{1,40}?)\s*[:：]\s*(.*)$", s)
        if m and label_target(m.group(1)):
            return True
        return bool(label_target(s)) and len(s) <= 40

    while i < n:
        s = lines[i]
        if s == END_MARK:
            i += 1
            continue
        m = re.match(r"^([^:：]{1,40}?)\s*[:：]\s*(.*)$", s)
        if m and label_target(m.group(1)):
            label, val = m.group(1), m.group(2)
            j = i + 1
            vals = [val] if val else []
            if not val:
                while j < n and not is_label_line(lines[j]) and (not vals or _continues(lines[j], label, strict=False)):
                    vals.append(lines[j]); j += 1
            else:
                while j < n and not is_label_line(lines[j]) and _continues(lines[j], label):
                    vals.append(lines[j]); j += 1
            pairs.append((label_target(label), label, "\n".join(vals)))
            i = j
            continue
        if label_target(s) and len(s) <= 40 and i + 1 < n and not is_label_line(lines[i + 1]):
            j, vals = i + 1, []
            while j < n and not is_label_line(lines[j]) and (not vals or _continues(lines[j], s, strict=False)):
                vals.append(lines[j]); j += 1
            pairs.append((label_target(s), s.rstrip(":"), "\n".join(vals)))
            i = j
            continue
        free.append(s)
        i += 1
    return pairs, free


ENQUIRY_RE = re.compile(r"^(business )?enquiry( form)?\b|^please (select|enter|fill|choose)\b|^send (meeting )?(request|enquiry)\b|^qty\b", re.I)


def _continues(line, label, strict=True):
    """A following plain line belongs to the same value for long text labels (about, address, profile).
    strict=False: only the stop rule is applied (used for a label that has no value on its own line)."""
    t = label_target(label)
    ln = clean(line)
    if ln == END_MARK or ENQUIRY_RE.match(ln):
        return False
    if ln.lower() in COUNTRIES or looks_like_domain(ln) or STAND_RE.match(ln) or EMAIL_RE.fullmatch(ln):
        return False                       # a country, website, stand or e-mail line is its own field
    if not strict:
        return True
    return t in ("About the Company", "Company Profile", "Address / Corporate Address", "Product", "Category",
                 "Subcategory", "Services")


# ---------------------------------------------------------------- links
def classify_links(el, base_url, own_dom):
    """Return list of dicts for every link inside el."""
    out = []
    anchors = ([el] if getattr(el, "name", "") == "a" and el.get("href") else []) + el.find_all("a", href=True)   # the card itself may be the link
    for a in anchors:
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript"):
            continue
        url = urljoin(base_url, href)
        text = clean(a.get_text(" "))
        low = href.lower()
        # redirect links (openURL.aspx?...&HyperLinkURL=https://company.com): the real website is inside the link
        mq = re.search(r"[?&](?:HyperLinkURL|redirect|url|u|target|link)=(https?(?::|%3A)[^&#]+)", href, re.I)
        if mq and not low.startswith(("mailto:", "tel:")):
            url = unquote(mq.group(1))
            low = url.lower()
        mb = re.search(r"[?&]MapItBooth=([^&#]+)", href, re.I)
        if mb:                                                    # "Map It" link: the text/param is the stand number
            out.append({"kind": "booth", "url": unquote(mb.group(1)), "text": text, "a": a, "domain": ""})
            continue
        d = domain_of(url)
        rd = root_domain(d) if d else ""
        if low.startswith("mailto:"):
            kind = "email"
        elif low.startswith("tel:"):
            kind = "tel"
        elif re.search(r"google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|openstreetmap\.org|bing\.com/maps", url, re.I):
            kind = "map"
        elif re.search(r"\.(pdf|docx?|pptx?|xlsx?|zip)(\?|#|$)", low):
            kind = "file"
        elif re.search(r"youtu\.be/|youtube\.com/(watch|embed|shorts|live|v/)|vimeo\.com/\d", url, re.I):
            kind = "video"
        elif rd in SOCIAL or d in SOCIAL:
            kind = "social"
        elif rd and rd == own_dom:
            kind = "internal"
        elif rd:
            kind = "external"
        else:
            kind = "other"
        out.append({"kind": kind, "url": url, "text": text, "a": a, "domain": rd})
    return out


def section_hint(a):
    """Words of the nearest heading / caption before a link (Press Release, Brochure, Video ...)."""
    h = a.find_previous(["h1", "h2", "h3", "h4", "h5", "h6", "caption", "legend"])
    t = clean(h.get_text(" ")).lower() if h else ""
    row = a.find_parent(["tr", "li"])
    rt = clean(row.get_text(" ")).lower() if row else ""
    return t, rt


def link_name(link):
    t = link["text"]
    a = link["a"]
    if len(t) < 3 or GENERIC_LINK_TEXT.match(t):
        tt = clean(a.get("title") or a.get("aria-label") or "")
        if tt and not GENERIC_LINK_TEXT.match(tt):
            return tt[:120]
        t = "" if len(t) < 3 else t
    if t and not GENERIC_LINK_TEXT.match(t) and len(t) <= 120:
        return t
    for holder in (a.parent, a.parent.parent if a.parent is not None else None):
        if holder is None:
            continue
        cap = holder.find(["figcaption", "h3", "h4", "h5", "h6", "strong", "b", "p", "span", "div"], string=True)
        if cap is not None and cap is not a and not cap.find("a"):
            ct = clean(cap.get_text(" "))
            if ct and not GENERIC_LINK_TEXT.match(ct) and not VIEW_WORDS.match(ct) and not DOWNLOAD_WORDS.search(ct) and len(ct) <= 120:
                return ct
    row = a.find_parent("tr")
    if row:
        for c in row.find_all(["td", "th"]):
            if c.find("a", href=True) is None:
                tx = clean(c.get_text(" "))
                if tx and not re.fullmatch(r"[\d.\s]+", tx):
                    return tx[:120]
    fn = urlparse(link["url"]).path.rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", re.sub(r"\.\w{2,4}$", "", fn)).strip() or "File"


def apply_links(rec, links, base_url, own_dom, name_text="", is_detail=False):
    """Put the links of a block into the record. Returns the internal detail link (or '')."""
    detail = ""
    for L in links:
        k, url, text = L["kind"], L["url"], L["text"]
        if k == "email":
            em = re.sub(r"^mailto:", "", url, flags=re.I).split("?")[0]
            if EMAIL_RE.fullmatch(em) and not rec["Email"]:
                rec["Email"] = em
        elif k == "tel":
            ph = re.sub(r"^tel:", "", url, flags=re.I)
            rec["extra"].append("Phone: " + clean(ph)) if ph else None
        elif k == "file":
            low = (text + " " + url).lower()
            hint, rowt = section_hint(L["a"])
            if "press" in low or "press" in hint or "press" in rowt:
                rec["Press Release"].append(f"{link_name(L)}: {url}")
            else:
                rec["Brochures"].append(f"{link_name(L)}: {url}")
        elif k == "booth":
            item = "Stand / Hall: " + clean(L["url"] or text)
            if item not in rec["extra"]:
                rec["extra"].append(item)
        elif k == "video":
            rec["Video"].append(f"{link_name(L)}: {url}" if text and not GENERIC_LINK_TEXT.match(text) else url)
        elif k == "social":
            nm = root_domain(domain_of(url)).split(".")[0].title()
            rec["extra"].append(f"{nm}: {url}")
        elif k == "external":
            low = text.lower()
            if not rec["Website"] and (re.search(r"website|www\.|visit|http|\.com|\.in\b|\.net|\.org|\.de|\.co", low)
                                       or (name_text and norm_url(text) == norm_url(name_text)) is False and
                                       (name_text and clean(name_text).lower() in low or not text)):
                rec["Website"] = url
            elif not rec["Website"] and (L["a"].find(["h1", "h2", "h3", "h4", "strong", "b"]) or text == name_text):
                rec["Website"] = url
            elif not rec["Website"] and len([x for x in links if x["kind"] == "external"]) == 1 and not is_detail:
                rec["Website"] = url
            else:
                low2 = (text + " " + url).lower()
                if "press" in low2:
                    rec["Press Release"].append(f"{link_name(L)}: {url}")
                elif "brochure" in low2 or "catalog" in low2:
                    rec["Brochures"].append(f"{link_name(L)}: {url}")
        elif k == "internal" and not detail and not is_detail:
            if re.search(r"view|detail|more|profile|know|read", text, re.I) or text == name_text or \
                    L["a"].find(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]) or not text or \
                    (name_text and clean(name_text).lower() in text.lower()) or \
                    re.search(r"(exhibitor|company|profile|detail)s?[-_/]", url, re.I) and \
                    len([x for x in links if x["kind"] == "internal"]) <= 2:
                detail = url
    return detail


# ---------------------------------------------------------------- records from lines
def fill_from_pairs(rec, pairs):
    cur = None
    for target, label, value in pairs:
        value = clean_multiline(value)
        if not value or target == IGNORE:
            continue
        if target in SINGLE:
            if target == "Website":
                m = re.search(r"https?://\S+|www\.\S+|[\w.\-]+\.[a-z]{2,}(?:/\S*)?", value, re.I)
                value = fix_url(m.group(0)) if m else value
            if target == "Email":
                m = EMAIL_RE.search(value)
                value = m.group(0) if m else ""
            if value and not rec[target]:
                rec[target] = value.replace("\n", " ") if target in ("Company Name", "Country") else value
        elif target in MULTI:
            comma = target in ("Category", "Subcategory") or bool(re.search(r"categor", label or "", re.I))
            parts = COMMA_SPLIT.split(value) if comma else MULTI_SPLIT.split(value)
            for p in parts:
                p = clean(p)
                if p:
                    rec[target].append(p)
        elif target == CONTACT_NAME:
            cur = {"Name": re.sub(r"^(contact\s+)?(person\s+)?name\s*[:\-]\s*", "", clean(value.split("\n")[0]), flags=re.I),
                   "Designation": "", "Phone": "", "Email": ""}
            rec["contacts"].append(cur)
        elif target == CONTACT_DESIG:
            if cur is None:
                cur = {"Name": "", "Designation": "", "Phone": "", "Email": ""}
                rec["contacts"].append(cur)
            cur["Designation"] = clean(value.split("\n")[0])
        elif target in (CONTACT_PHONE, COMPANY_PHONE):
            ph = clean(value.split("\n")[0])
            if cur is not None and not cur["Phone"] and target == CONTACT_PHONE or \
                    (cur is not None and not cur["Phone"] and target == COMPANY_PHONE and cur["Name"]):
                cur["Phone"] = ph
            else:
                rec["extra"].append("Phone: " + ph)
        elif target == "@extra":
            rec["extra"].append(f"{clean(label).rstrip(':').title()}: {clean(value.replace(chr(10), ' ; '))}")
    for c in rec["contacts"]:
        pass


DOMAIN_LIKE = re.compile(r"^(https?://)?(www\.)?[\w\-]+(\.[\w\-]+)+(/\S*)?$", re.I)


def looks_like_domain(t):
    t = clean(t)
    return bool(t) and " " not in t and bool(DOMAIN_LIKE.match(t))


def _first_text(el, selectors):
    for sel in selectors:
        for t in el.select(sel):
            tx = clean(t.get_text(" "))
            if tx and len(tx) <= 150 and not label_target(tx) and not looks_like_domain(tx):
                return tx
    return ""


COUNTRIES = {c.replace("_", " ").lower() for c in """Afghanistan Albania Algeria Argentina Armenia Australia Austria Azerbaijan Bahrain Bangladesh Belarus Belgium Bhutan Bolivia Bosnia Brazil Bulgaria Cambodia Canada Chile China Colombia Croatia Cyprus Czechia Czech_Republic Denmark Ecuador Egypt Estonia Ethiopia Finland France Georgia Germany Ghana Greece Hong_Kong Hungary Iceland India Indonesia Iran Iraq Ireland Israel Italy Japan Jordan Kazakhstan Kenya Korea South_Korea Kuwait Latvia Lebanon Libya Lithuania Luxembourg Malaysia Maldives Malta Mauritius Mexico Mongolia Morocco Myanmar Nepal Netherlands New_Zealand Nigeria Norway Oman Pakistan Peru Philippines Poland Portugal Qatar Romania Russia Saudi_Arabia Serbia Singapore Slovakia Slovenia South_Africa Spain Sri_Lanka Sweden Switzerland Syria Taiwan Tanzania Thailand Tunisia Turkey Turkiye UAE Uganda Ukraine United_Arab_Emirates United_Kingdom UK United_States USA US Uruguay Uzbekistan Venezuela Vietnam Yemen Zambia Zimbabwe Türkiye Hong_Kong_SAR_of_China Hong_Kong_SAR Macao Macau Czech Slovak_Republic Korea_(Republic_of) Republic_of_Korea Luxemburg Moldova North_Macedonia Montenegro Kosovo Liechtenstein Monaco Andorra San_Marino Cuba Costa_Rica Panama Dominican_Republic Guatemala Honduras Jamaica Trinidad_and_Tobago Paraguay Senegal Cameroon Ivory_Coast Angola Mozambique Namibia Botswana Rwanda Sudan Mali Niger Madagascar Laos Brunei Kyrgyzstan Tajikistan Turkmenistan Palestine Papua_New_Guinea Fiji Greenland Faroe_Islands Isle_of_Man Gibraltar Jersey Guernsey""".split()}
STAND_RE = re.compile(r"^(stand|booth|stall|hall)\b.*", re.I)
BROWSER_JUNK = re.compile(r"outdated browser|cannot be viewed in this browser|browser (is|are) (too old|not supported|outdated)|"
                          r"(update|upgrade) your browser|please enable javascript|javascript is (disabled|required)", re.I)
# a bare stand number or several ("B5.280", "B5.430, B5.530", "Hall 4 / A12") or just a dash
BOOTH_ONLY = re.compile(r"^(?:[-–—.]+|[A-Za-z]{0,2}\d{1,2}[.\-/]\d{1,4}[A-Za-z]?(?:\s*[,;/&]\s*[A-Za-z]{0,2}\d{1,2}[.\-/]\d{1,4}[A-Za-z]?)*)$")


def record_from_block(el, base_url, own_dom, want_name=True):
    rec = new_record()
    rec["detail_url"] = ""
    lines = [ln for ln in block_lines(el) if not BROWSER_JUNK.search(ln)]
    pairs, free = parse_lines(lines)
    name = ""
    if want_name:
        name = _first_text(el, ["h1", ".h1", "h2", "h3", "h4", "h5", "h6", "[class*=name i]", "[class*=title i]",
                                "[class*=company i]", "strong", "b"])
        if name and BOOTH_ONLY.match(name):                 # a stand number is not a name
            name = ""
        if not name:      # first plain line that is not a label/value and not a web address
            for ln in free[:6]:
                if ln and len(ln) <= 150 and not looks_like_domain(ln) and not label_target(ln) \
                        and not EMAIL_RE.fullmatch(ln) and not STAND_RE.match(ln) and not BOOTH_ONLY.match(ln):
                    name = ln
                    break
        if not name:
            for a in el.find_all("a"):
                tx = clean(a.get_text(" "))
                if tx and len(tx) <= 150 and not label_target(tx) and not GENERIC_LINK_TEXT.match(tx) \
                        and not looks_like_domain(tx):
                    name = tx
                    break
    fill_from_pairs(rec, pairs)
    name = BULLET.sub("", name) if name else name
    if name and not rec["Company Name"]:
        rec["Company Name"] = name
    if not rec["About the Company"] and not rec["Company Profile"]:        # a long plain paragraph = about the company
        paras = [ln for ln in free if len(ln) >= 80 and len(ln.split()) >= 12 and not label_target(ln)
                 and not looks_like_domain(ln) and ln != name]
        if paras:
            rec["About the Company"] = max(paras, key=len)
    links = classify_links(el, base_url, own_dom)
    rec["detail_url"] = apply_links(rec, links, base_url, own_dom, rec["Company Name"])
    # logo: first real image
    for img in el.find_all("img"):
        src = (img.get("data-src") or img.get("data-lazy-src") or img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        if re.search(r"flag|icon|sprite|arrow|loader|spinner|\.svg(\?|$)", src, re.I) and "logo" not in src.lower():
            continue
        w = img.get("width", "")
        if w.isdigit() and int(w) < 20:
            continue
        rec["Logo / Image"] = urljoin(base_url, src)
        break
    # Brochures / Press Release / Video are links only: drop plain-text items (the link version is kept)
    for f in ("Brochures", "Press Release", "Video"):
        rec[f] = [x for x in rec[f] if re.search(r"https?://", x)]
    # lines without a label: a country name, or a stand / hall line
    for fi, ln in enumerate(free):
        if not rec["Country"] and ln.lower() in COUNTRIES:
            rec["Country"] = ln
        elif BOOTH_ONLY.match(ln) and not re.fullmatch(r"[-–—.]+", ln) and ln != name:
            item = "Stand / Hall: " + ln                   # a bare booth number such as B5.280
            if item not in rec["extra"]:
                rec["extra"].append(item)
        elif STAND_RE.match(ln) and len(ln) <= 60:
            if re.search(r"[:：]\s*$", ln) and fi + 1 < len(free):      # label on one line, value on the next
                ln = ln.rstrip(": ：") + ": " + free[fi + 1]
            item = "Stand / Hall: " + re.sub(r"^(stand|booth|hall)[^:：]*[:：]\s*", "", ln, flags=re.I) \
                if re.match(r"^(stand|booth|hall)[^:：]*[:：]\s*\S", ln, re.I) else "Stand / Hall: " + ln
            if item not in rec["extra"]:
                rec["extra"].append(item)
    for t in el.select("[class*=meta-line1]"):                       # the show the company exhibits at (ees Europe ...)
        tx = clean(t.get_text(" "))
        if tx and len(tx) <= 80 and "Exhibition: " + tx not in rec["extra"]:
            rec["extra"].append("Exhibition: " + tx)
    # email in plain text
    if not rec["Email"]:
        m = EMAIL_RE.search(" ".join(lines))
        if m:
            rec["Email"] = m.group(0)
    # free text that looks like a website
    if not rec["Website"]:
        for s in free[:8]:
            if re.fullmatch(r"(https?://|www\.)\S+", s, re.I):
                rec["Website"] = fix_url(s)
                break
    # a name that is really a url or a label is no name
    if re.fullmatch(r"(https?://|www\.)\S+", rec["Company Name"], re.I):
        rec["Company Name"] = ""
    rec["extra"] = list(dict.fromkeys(rec["extra"]))
    return rec


# ---------------------------------------------------------------- tables
def _headless_table(rows):
    """A table with no heading row: company names in one column and country names in another."""
    grid = []
    for tr in rows:
        cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
        grid.append([clean(c.get_text(" ")) for c in cells])
    grid = [g for g in grid if len(g) >= 2]
    if len(grid) < 5:
        return []
    width = max(len(g) for g in grid)
    cc = None
    for j in range(width):
        col = [g[j] for g in grid if j < len(g) and g[j]]
        if len(col) >= 5 and sum(1 for v in col if v.lower() in COUNTRIES) >= 0.6 * len(col):
            cc = j
            break
    if cc is None:
        return []
    nc = None
    for j in range(width):
        if j == cc:
            continue
        col = [g[j] for g in grid if j < len(g) and g[j]]
        if len(col) >= 5 and sum(1 for v in col if _name_like(v) and not re.fullmatch(r"[\d .,-]+", v)) >= 0.8 * len(col):
            nc = j
            break
    if nc is None:
        return []
    recs = []
    for g in grid:
        if nc >= len(g) or not g[nc] or heading_target(g[nc]) == "Company Name" or g[nc].lower() == "country":
            continue
        rec = new_record(); rec["detail_url"] = ""
        rec["Company Name"] = g[nc]
        if cc < len(g) and g[cc].lower() in COUNTRIES:
            rec["Country"] = g[cc]
        recs.append(rec)
    return recs


def parse_tables(soup, base_url, own_dom):
    best = []
    for table in soup.find_all("table"):
        if table.find_parent(list(NAV_TAGS)):
            continue
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        hi, heads, targets = 0, [], []
        for k, tr0 in enumerate(rows[:6]):             # the heading row may come after a title row
            hd = tr0.find_all(["th", "td"])
            hh = [clean(h.get_text(" ")) for h in hd]
            tt = [heading_target(h) for h in hh]
            if len(hh) >= 2 and "Company Name" in tt:
                hi, heads, targets = k, hh, tt
                break
        data_rows = rows[hi + 1:]
        if not heads:
            guess = _headless_table(rows)
            if guess:
                recs_g = guess
                if len(recs_g) > len(best):
                    best = recs_g
            continue
        recs = []
        for tr in data_rows:
            cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            rec = new_record(); rec["detail_url"] = ""
            for j, c in enumerate(cells):
                if j >= len(heads):
                    break
                tgt = targets[j] if j < len(targets) else None
                txt = clean_multiline(c.get_text("\n"))
                links = classify_links(c, base_url, own_dom)
                if tgt == IGNORE:
                    continue
                if tgt is None:
                    if txt and heads[j]:
                        rec["extra"].append(f"{heads[j].title()}: {txt.replace(chr(10), ' ; ')}")
                    continue
                if tgt == "Company Name":
                    rec["Company Name"] = clean(txt.split("\n")[0])
                    for L in links:
                        if L["kind"] == "internal" and not rec["detail_url"]:
                            rec["detail_url"] = L["url"]
                        elif L["kind"] == "external" and not rec["Website"]:
                            rec["Website"] = L["url"]
                    continue
                if tgt == "Website":
                    ext = [L for L in links if L["kind"] == "external"]
                    if ext:
                        rec["Website"] = ext[0]["url"]
                    else:
                        fill_from_pairs(rec, [(tgt, heads[j], txt)])
                    continue
                if tgt in ("Brochures", "Press Release", "Video"):
                    ls = [L for L in links if L["kind"] in ("file", "video", "external", "internal")]
                    for L in ls:
                        rec[tgt].append(f"{link_name(L)}: {L['url']}" if L["kind"] != "video" else L["url"])
                    if not ls and txt:
                        fill_from_pairs(rec, [(tgt, heads[j], txt)])
                    continue
                if tgt == "Email":
                    em = [re.sub(r"^mailto:", "", L["url"], flags=re.I) for L in links if L["kind"] == "email"]
                    if em:
                        rec["Email"] = em[0]
                        continue
                fill_from_pairs(rec, [(tgt, heads[j], txt)])
            if rec["Company Name"]:
                recs.append(rec)
        if len(recs) > len(best):
            best = recs
    return best


# ---------------------------------------------------------------- cards
def _in_nav(el):
    return any(p.name in NAV_TAGS for p in el.parents if isinstance(p, Tag))


FILTER_RE = re.compile(r"facet|filter|dropdown|sidebar|breadcrumb|pager|pagination|popular|category-list|alphabet|a-z", re.I)
COUNT_SUFFIX = re.compile(r"\(\s*\d+\s*\)\s*$")


def _in_filter(el):
    for p in [el] + [x for x in el.parents if isinstance(x, Tag)]:
        if FILTER_RE.search(" ".join(p.get("class", [])) + " " + (p.get("id") or "") + " " +
                            " ".join(k for k in p.attrs if str(k).startswith("data-drupal-facet"))):
            return True
    return False


def find_card_group(soup):
    """The biggest group of repeated sibling blocks that look like exhibitor cards."""
    best, best_score = None, 0
    for parent in soup.find_all(True):
        if parent.name in SKIP_TAGS or parent.name in ("table", "tbody", "thead", "tr", "select", "ul", "ol") and False:
            continue
        if parent.name in SKIP_TAGS or _in_nav(parent) or parent.name in NAV_TAGS or _in_filter(parent):
            continue
        kids = [c for c in parent.children if isinstance(c, Tag) and c.name not in SKIP_TAGS]
        if len(kids) < 4:
            continue
        groups = {}
        for c in kids:
            if c.name in ("tr", "td", "th", "br", "hr", "option", "img", "span", "p", "b", "i", "strong", "em", "button", "input", "label"):
                continue
            sig = (c.name, tuple(sorted(c.get("class", []))))
            groups.setdefault(sig, []).append(c)
        for sig, items in groups.items():
            if len(items) < 4:
                continue
            texts = [clean(x.get_text(" ")) for x in items]
            if sum(1 for t in texts if COUNT_SUFFIX.search(t)) > 0.5 * len(texts):      # "Adhesives (21)" = a filter
                continue
            ok = [t for t in texts if 5 <= len(t) <= 4000]
            if len(ok) < 0.8 * len(items):
                continue
            if len(set(ok)) < 0.8 * len(ok):
                continue
            avg = sum(len(t) for t in ok) / len(ok)
            if avg < 8:
                continue
            with_link = sum(1 for x in items if x.find(["a", "h1", "h2", "h3", "h4", "h5", "h6", "img"]))
            score = len(items) * (1 + 0.5 * with_link / len(items))
            if score > best_score:
                best, best_score = items, score
    return best or []


ISO2 = {"in": "India", "cn": "China", "de": "Germany", "it": "Italy", "us": "United States", "gb": "United Kingdom",
        "uk": "United Kingdom", "fr": "France", "es": "Spain", "jp": "Japan", "kr": "South Korea", "tw": "Taiwan",
        "tr": "Turkey", "ch": "Switzerland", "nl": "Netherlands", "be": "Belgium", "at": "Austria", "se": "Sweden",
        "dk": "Denmark", "fi": "Finland", "no": "Norway", "pl": "Poland", "cz": "Czech Republic", "pt": "Portugal",
        "gr": "Greece", "ae": "United Arab Emirates", "sa": "Saudi Arabia", "sg": "Singapore", "my": "Malaysia",
        "th": "Thailand", "id": "Indonesia", "vn": "Vietnam", "pk": "Pakistan", "bd": "Bangladesh", "lk": "Sri Lanka",
        "np": "Nepal", "au": "Australia", "nz": "New Zealand", "ca": "Canada", "mx": "Mexico", "br": "Brazil",
        "ar": "Argentina", "za": "South Africa", "eg": "Egypt", "il": "Israel", "ru": "Russia", "ua": "Ukraine",
        "hk": "Hong Kong", "ie": "Ireland", "hu": "Hungary", "ro": "Romania", "bg": "Bulgaria", "lt": "Lithuania",
        "lv": "Latvia", "ee": "Estonia", "si": "Slovenia", "sk": "Slovakia", "hr": "Croatia", "rs": "Serbia",
        "ir": "Iran", "qa": "Qatar", "kw": "Kuwait", "om": "Oman", "bh": "Bahrain", "jo": "Jordan", "ma": "Morocco"}


def parse_cards(soup, base_url, own_dom):
    items = find_card_group(soup)
    recs = []
    for el in items:
        rec = record_from_block(el, base_url, own_dom)
        if rec["Company Name"]:
            if not rec.get("Country"):                       # country shown only as a flag icon: class "flag-in"
                for fl in el.find_all(class_=True):
                    m = next((re.fullmatch(r"flag-([a-z]{2})", c) for c in fl.get("class", [])
                              if re.fullmatch(r"flag-([a-z]{2})", c)), None)
                    if m and m.group(1) in ISO2:
                        rec["Country"] = ISO2[m.group(1)]
                        break
            recs.append(rec)
    return recs


# ---------------------------------------------------------------- plain list of names
NAV_WORDS = {"home", "about", "about us", "contact", "contact us", "login", "sign in", "register", "faq", "faqs",
             "privacy policy", "terms", "terms & conditions", "sitemap", "gallery", "visit", "visitors",
             "exhibit", "exhibitors", "media", "news", "blog", "menu", "search", "back to top", "download",
             "tickets", "venue", "sponsors", "partners", "speakers", "agenda", "programme", "program"}
NAV_WORDS |= {"exhibitor", "visitor", "exhibitor profiles", "visitor profiles", "exhibitor list", "services",
              "follows us", "follow us", "head office", "facebook", "instagram", "linkedin", "twitter", "youtube",
              "terms and conditions", "terms & conditions", "t&c", "water pages t&c", "quick links", "useful links",
              "our offices", "reach us", "subscribe", "newsletter", "social media", "copyright", "all rights reserved"}
STOP_WORDS = {"services", "follows us", "follow us", "head office", "contact us", "quick links", "useful links",
              "our offices", "reach us", "subscribe", "newsletter", "social media", "registered office",
              "corporate office", "organised by", "organized by", "supported by", "media partners"}
JUNK_RE = re.compile(r"[,;]\s*$|^(?:\d{4}\s+)?exhibitors?\s+lists?(?:\s+\d{4})?$|^(?:list of\s+)?exhibitors?(?:\s+\d{4})?$|\b\d{5,6}\b|^©|\ball rights reserved\b", re.I)
SPLIT_TAIL = re.compile(r"(?:(?:solutions|technologies|systems|industries|engineers|engineering|enterprises|services|"
                        r"products|equipments?)\s+)?(?:pvt\.?|private|ltd\.?|limited|llp|inc\.?|corp\.?|\(p\))"
                        r"(?:\s+(?:ltd\.?|limited|\(p\)|\(india\)))*", re.I)
LEGAL_END = re.compile(r"\b(pvt|private|ltd|limited|llp|inc|corp|company|co|gmbh|llc)\.?\)?$", re.I)


def merge_split_names(names):
    """'Indian Centrifuge Engineering' + 'Solutions Pvt Ltd' (one name broken over two lines) -> one name."""
    out = []
    for n in names:
        if out and SPLIT_TAIL.fullmatch(n.strip()) and not LEGAL_END.search(out[-1]):
            out[-1] = out[-1].rstrip() + " " + n
        else:
            out.append(n)
    return out


def cut_at_stop(texts, keep_min=8):
    """The list ends at a footer heading (SERVICES, FOLLOWS US, HEAD OFFICE ...)."""
    for i, t in enumerate(texts):
        if i >= keep_min and BULLET.sub("", clean(t)).lower().rstrip(":") in STOP_WORDS:
            return texts[:i]
    return texts


BULLET = re.compile(r"^\s*(\d{1,4}\s*[.)\-:](?=\s|$)|[-•·*▪►»](?=\s|$))\s*")


def _plain_ok(texts):
    texts = cut_at_stop([BULLET.sub("", clean(t)) for t in texts])
    texts = [t for t in texts if t]
    if len(texts) < 8 or sum(1 for t in texts if COUNT_SUFFIX.search(t)) > 0.3 * len(texts):
        return []
    texts = [t for t in texts if t.lower() not in NAV_WORDS and not JUNK_RE.search(t)]
    texts = merge_split_names(texts)
    good = [t for t in texts if 2 <= len(t) <= 100 and re.search(r"[A-Za-z]", t) and not t.endswith(".") or
            (len(t) <= 60 and re.search(r"(pvt|ltd|limited|inc|llp|gmbh|corp|srl|spa|sas|snc)\.?$", t, re.I)) or
            (len(t) <= 60 and re.search(r"(?:\b[A-Za-z]\.){2,}$", t))]
    if len(good) < 0.8 * len(texts) or len(good) < 8:
        return []
    return good


def parse_plain(soup, base_url, own_dom):
    best_texts, best_els = [], None
    cands = []
    for parent in soup.find_all(True):
        if parent.name in SKIP_TAGS or parent.name in NAV_TAGS or _in_nav(parent):
            continue
        for tag in ("li", "p", "div"):
            kids = [c for c in parent.children if isinstance(c, Tag) and c.name == tag]
            if len(kids) >= 8:
                if tag == "div" and any(k.find(["div", "table", "ul", "li", "p"]) for k in kids):
                    continue
                cands.append(("els", kids, [k.get_text(" ") for k in kids]))
        if parent.name in ("p", "div", "td", "span", "section", "article") and len(parent.find_all("br", recursive=False)) >= 8:
            for br in parent.find_all("br", recursive=False):
                br.replace_with(NavigableString("\n"))
            cands.append(("lines", None, parent.get_text("\n").split("\n")))
    for table in soup.find_all("table"):          # tables without a heading row: only names (or no. + name)
        if _in_nav(table) or table.find("table"):
            continue
        texts = []
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if len(cells) == 1:
                texts.append(cells[0])
            elif len(cells) == 2 and re.fullmatch(r"\d{1,4}\.?", cells[0]):
                texts.append(cells[1])
        if texts:
            cands.append(("lines", None, texts))
    for kind, els, texts in cands:
        good = _plain_ok(texts)
        if len(good) > len(best_texts):
            best_texts, best_els = good, (kind, els)
    if not best_texts:
        return []
    recs = []
    kind, els = best_els
    if kind == "els":
        raw = [BULLET.sub("", clean(el.get_text(" "))) for el in els]
        stop = next((i for i, t in enumerate(raw) if i >= 8 and t.lower().rstrip(":") in STOP_WORDS), len(els))
        els = els[:stop]
        for el in els:
            tx = BULLET.sub("", clean(el.get_text(" ")))
            if tx.lower() in NAV_WORDS or not tx or JUNK_RE.search(tx):
                continue
            rec = record_from_block(el, base_url, own_dom)
            rec["Company Name"] = tx if (not rec["Company Name"] or looks_like_domain(rec["Company Name"])
                                         or len(tx) <= 100 and not label_target(tx) and ":" not in tx) else rec["Company Name"]
            if rec["Company Name"] and 2 <= len(rec["Company Name"]) <= 150:
                recs.append(rec)
    else:
        for t in best_texts:
            rec = new_record(); rec["detail_url"] = ""; rec["Company Name"] = t
            recs.append(rec)
    return _merge_split_recs(recs)


def _merge_split_recs(recs):
    out = []
    for r in recs:
        n = r["Company Name"]
        if out and SPLIT_TAIL.fullmatch(n.strip()) and not LEGAL_END.search(out[-1]["Company Name"]) \
                and not (r.get("Website") or r.get("detail_url") or out[-1].get("Website") or out[-1].get("detail_url")):
            out[-1]["Company Name"] = out[-1]["Company Name"].rstrip() + " " + n
        else:
            out.append(r)
    return out


def _core_sig(els):
    """(tag, classes that ALL the blocks share). Blocks often differ in odd/even/first/last classes."""
    from collections import Counter
    tag = Counter(x.name for x in els).most_common(1)[0][0]
    same = [set(x.get("class", [])) for x in els if x.name == tag]
    core = set.intersection(*same) if same else set()
    return (tag, tuple(sorted(core)))


def card_sig(html):
    """Tag + shared classes of the repeated company blocks of a list page (used to read the next pages the same way)."""
    items = find_card_group(_soup(html))
    if not items:
        return None
    return _core_sig(items)


def parse_cards_sig(html, base_url, sig):
    """Company blocks with the same tag+classes as on page 1 - works even if this page has only 1-3 of them."""
    soup = _soup(html)
    own = own_domain(base_url)
    recs = []
    core = set(sig[1])
    same = lambda x: isinstance(x, Tag) and x.name == sig[0] and (
        core <= set(x.get("class", [])) if core else not x.get("class"))
    for el in soup.find_all(sig[0]):
        if not same(el) or _in_nav(el) or _in_filter(el) or any(same(p) for p in el.parents):
            continue
        rec = record_from_block(el, base_url, own)
        if rec["Company Name"]:
            fl = next((c[5:] for t in el.find_all(class_=True) for c in t.get("class", []) if re.fullmatch(r"flag-[a-z]{2}", c)), "")
            if not rec.get("Country") and fl in ISO2:
                rec["Country"] = ISO2[fl]
            recs.append(rec)
    return recs


# ---------------------------------------------------------------- entries found by their heading / name element
NAME_CLASS = re.compile(r"name|title|company|exhibitor", re.I)


def find_marker_entries(soup):
    """Entries that have no common wrapper (flat: heading, text, text, heading ...) or a wrapper that is not
    repeated cleanly. The marker is the element holding the company name (a heading, or an element whose class
    says name/title/company). Each entry = the marker + what belongs to it."""
    groups = {}
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"] + [True]):
        if not isinstance(el, Tag) or el.name in SKIP_TAGS or _in_nav(el) or _in_filter(el):
            continue
        if el.name not in ("h1", "h2", "h3", "h4", "h5", "h6") and not (
                el.name in ("div", "span", "p", "a", "strong", "b") and NAME_CLASS.search(" ".join(el.get("class", [])))):
            continue
        sig = (el.name, tuple(sorted(el.get("class", []))))
        groups.setdefault(sig, []).append(el)
    best, best_n = [], 0
    for sig, els in groups.items():
        els = [e for e in els if not any(p in els for p in e.parents)]          # not nested in each other
        texts = [clean(e.get_text(" ")) for e in els]
        good = [e for e, t in zip(els, texts) if _name_like(t) and not label_target(t) and not GENERIC_LINK_TEXT.match(t)]
        if len(good) < 4 or len(good) < 0.8 * len(els):
            continue
        if len({clean(e.get_text(" ")).lower() for e in good}) < 0.8 * len(good):
            continue
        if len(good) > best_n:
            best, best_n = good, len(good)
    if not best:
        return []
    ids = {id(e) for e in best}
    def n_markers(node):
        return sum(1 for d in node.find_all(True) if id(d) in ids) + (1 if id(node) in ids else 0)
    entries = []
    for m in best:
        node = m
        while node.parent is not None and node.parent.name not in ("body", "html", "[document]") \
                and n_markers(node.parent) == 1:
            node = node.parent
        if node is m and m.parent is not None:                  # flat: marker + following siblings up to the next marker
            wrap = soup.new_tag("div")
            m.insert_before(wrap)
            sibs = [m]
            for sib in list(m.next_siblings):
                if isinstance(sib, Tag) and (id(sib) in ids or any(id(d) in ids for d in sib.find_all(True))):
                    break
                sibs.append(sib)
            for x in sibs:
                wrap.append(x.extract() if hasattr(x, "extract") else x)
            node = wrap
        entries.append(node)
    return entries


def find_link_entries(soup, own_dom):
    """Entries found by their website link: every exhibitor has ONE link to its own site. The entry is the
    biggest block around that link which holds no other company link."""
    anchors, doms = [], {}
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if not re.match(r"^https?://", h, re.I) or _in_nav(a) or any(p.name in SKIP_TAGS for p in a.parents if isinstance(p, Tag)):
            continue
        d = company_domain(h)
        if not d or root_domain(d) == own_dom:
            continue
        anchors.append(a)
        doms.setdefault(d, []).append(a)
    uniq = [v[0] for v in doms.values()]
    if len(uniq) < 4:
        return []
    ids = {id(a) for a in anchors}
    def n_links(node):
        return sum(1 for d in node.find_all("a", href=True) if id(d) in ids)
    cards, seen = [], set()
    for a in uniq:
        node = a
        while node.parent is not None and node.parent.name not in ("body", "html", "[document]") and n_links(node.parent) == 1:
            node = node.parent
        if node is a or id(node) in seen:
            continue
        seen.add(id(node))
        cards.append(node)
    lens = sorted(len(clean(c.get_text(" "))) for c in cards)
    if not lens:
        return []
    med = lens[len(lens) // 2]
    cards = [c for c in cards if 20 <= len(clean(c.get_text(" "))) <= max(6 * med, 300)]
    return cards if len(cards) >= 4 else []


def _richness(recs):
    return sum(1 for r in recs if any(v for k, v in r.items() if k not in ("Company Name", "detail_url"))) / max(len(recs), 1)


def _container_sig(entries):
    """Tag + classes shared by (nearly) all entry blocks, or None."""
    from collections import Counter
    top = Counter(x.name for x in entries).most_common(1)[0]
    if top[1] < 0.8 * len(entries):
        return None
    sig = _core_sig([x for x in entries if x.name == top[0]])
    return sig if sig[1] else None


def _entries_core(soup, base_url, own_dom, html=None):
    """(records, container signature) of the best entry reading, or ([], None)."""
    best, best_entries = [], []
    for finder in (lambda: find_marker_entries(soup), lambda: find_link_entries(_soup(str(soup)), own_dom)):
        try:
            entries = finder()
        except Exception:
            entries = []
        if len(entries) < 4:
            continue
        recs = [r for r in (record_from_block(e, base_url, own_dom) for e in entries) if r["Company Name"]]
        if len(recs) < 4 or _richness(recs) < 0.6:
            continue
        if len({norm_name(r["Company Name"]) for r in recs}) < 0.8 * len(recs):      # names must differ
            continue
        if len(recs) > len(best):
            best, best_entries = recs, entries
    if not best:
        return [], None
    sig = _container_sig(best_entries)
    if sig and html:                                    # entries that have no website link / heading still have the block
        every = parse_cards_sig(html, base_url, sig)
        mine = {norm_name(r["Company Name"]) for r in best}
        if len(every) > len(best) and sum(1 for r in every if norm_name(r["Company Name"]) in mine) >= 0.8 * len(best):
            best = every
    return best, sig


def entries_sig(html, base_url):
    """The block signature of the entries of this page (to read the next pages the same way), or None."""
    return _entries_core(_soup(html), base_url, own_domain(base_url), html)[1]


def parse_entries(soup, base_url, own_dom, normal, html=None):
    """Records from entries found by their name heading, or else by their website link. Used when the entries
    carry exhibitor-type data (website / country / booth ...) AND the normal result is something else (a product
    list inside one entry, every line of every entry ...) or is only the names of the same entries."""
    best, _ = _entries_core(soup, base_url, own_dom, html)
    if not best:
        return []
    if normal:
        mine = {norm_name(r["Company Name"]) for r in best}
        same = sum(1 for r in normal if norm_name(r["Company Name"]) in mine) >= 0.5 * len(normal)
        if same and len(normal) >= len(best) and _richness(normal) >= _richness(best) - 0.2:
            return []                                   # the normal reader found this same list, as well or better
    return best


# ---------------------------------------------------------------- grid of logos (name only in alt/title, or none)
def _name_from_domain(dom):
    label = dom.split(".")[0] if dom else ""
    label = re.sub(r"^(www|in|the)(?=[-_])", "", label)
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", label) if w)


def parse_logos(soup, base_url, own_dom):
    """Logos that link to the company's own website. Name: image alt/title/aria-label or link text;
    if the site gives none, the name is made from the website address (marked for checking).
    Logos with no link at all (javascript:void, #) are kept too (name only) when they sit in the same
    kind of block as the linked ones."""
    def pick_name(a, img):
        txt = clean(a.get_text(" "))
        for cand in (a.get("title"), a.get("aria-label"), img.get("alt"), img.get("title"), txt):
            c = clean(cand or "")
            if c and not GENERIC_LINK_TEXT.match(c) and not re.fullmatch(r"(logo|image|img|brand|photo|exhibitor)\W*\d*", c, re.I) \
                    and not re.search(r"\.(png|jpe?g|gif|svg|webp)$", c, re.I) and len(c) <= 120:
                return c
        return ""

    def sig(a):
        p = a.parent
        return (p.name, tuple(sorted(p.get("class", [])))) if p else None

    linked, unlinked, sigs = [], [], set()
    for a in soup.find_all("a", href=True):
        if _in_nav(a) or any(p.name in SKIP_TAGS for p in a.parents if isinstance(p, Tag)):
            continue
        img = a.find("img")
        if not img:
            continue
        href = a["href"].strip()
        if re.match(r"^(https?:)?//", href, re.I):
            url = norm_url(href) or href
            d = company_domain(url)
            if d == own_dom:
                continue
            if not d:                                   # a Facebook / LinkedIn page: keep the name, link in Extra
                unlinked.append((a, img, href))
                continue
            linked.append((a, img, href, url, d))
            sigs.add(sig(a))
        elif href.lower().startswith("javascript:") or href in ("#", ""):
            unlinked.append((a, img, ""))
    if len(linked) < 8:
        return []
    out, seen = [], set()
    for a, img, href, url, d in linked:
        name = pick_name(a, img)
        key = norm_name(name) if name else d
        if not key or key in seen:
            continue
        seen.add(key)
        rec = new_record(); rec["detail_url"] = ""
        rec["Website"] = href
        if name:
            rec["Company Name"] = name
        else:
            rec["Company Name"] = _name_from_domain(domain_of(url))
            rec["extra"].append("Check: company name made from the website address (the site shows only a logo)")
        if rec["Company Name"]:
            out.append(rec)
    for a, img, soc in unlinked:
        name = pick_name(a, img)
        key = norm_name(name) if name else ""
        if not key or key in seen or sig(a) not in sigs:
            continue
        seen.add(key)
        rec = new_record(); rec["detail_url"] = ""; rec["Company Name"] = name
        if soc:
            rec["extra"].append("Social: " + soc)
        out.append(rec)
    return out


# ---------------------------------------------------------------- run of names in page order
BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "section", "article",
              "ul", "ol", "dd", "dt", "blockquote", "figure", "address", "main", "table", "span.block"]


def _name_like(t):
    if "\x01" in t or COUNT_SUFFIX.search(clean(t)) or JUNK_RE.search(clean(t)):
        return False
    t = BULLET.sub("", clean(t))
    if not (2 <= len(t) <= 100) or not re.search(r"[A-Za-z]", t):
        return False
    if t.lower() in NAV_WORDS or "©" in t or "@" in t or len(t.split()) > 12:
        return False
    if t.endswith(".") and not re.search(r"(pvt|ltd|inc|co|corp|llp|limited|srl|spa|sas|snc|gmbh|bv|ag|sa|sl|kg|ltda|plc)\.$", t, re.I) \
            and not re.search(r"(?:\b[A-Za-z]\.){2,}$", t):
        return False
    t2 = re.sub(r"\b(pvt|ltd|inc|co|corp|llp|no|st|dr|mr|m/s)\.", "", t, flags=re.I)
    t2 = re.sub(r"(?:\b[A-Za-z]\.){2,}", "", t2)
    if re.search(r"\.\s+[A-Z]", t2) or re.search(r"https?://|www\.", t, re.I):
        return False
    if re.fullmatch(r"[\d\s/:.\-,]+", t) or re.search(r"\b(19|20)\d\d\b.*\b(19|20)\d\d\b", t):
        return False
    return True


def parse_runs(soup, min_len=8):
    """All text lines of the page in reading order (every p / li / br / div is a line), then the longest
    stretch of name-like lines. Joins a list that the site split into several blocks or columns."""
    s = BeautifulSoup(str(soup), "lxml")
    for t in s.find_all(list(SKIP_TAGS | NAV_TAGS)):
        t.decompose()
    for t in s.find_all(True):
        if t.attrs is not None and _is_hidden(t):
            t.decompose()
    hs = s.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "th"])
    from collections import Counter
    many = {k for k, v in Counter(h.name for h in hs).items() if v >= 15}   # a tag used 15+ times holds the names
    for h in hs:
        if h.name not in many:
            h.insert(0, NavigableString("\x01"))          # other headings are never company names
    for br in s.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for t in s.find_all([b for b in BLOCK_TAGS if "." not in b]):
        t.insert_before(NavigableString("\n"))
        t.insert_after(NavigableString("\n"))
    lines = [clean(x) for x in s.get_text().split("\n")]
    lines = [x for x in lines if x]
    best, cur, gap = [], [], 0
    for ln in lines:
        if len(cur) >= 8 and BULLET.sub("", ln).lower().rstrip(":").replace("\x01", "") in STOP_WORDS:
            if len(cur) > len(best):
                best = cur
            cur, gap = [], 0
            continue
        if _name_like(ln):
            cur.append(BULLET.sub("", ln)); gap = 0
        else:
            gap += 1
            if len(ln) > 1 and gap > 2 or len(ln) > 40:      # a sentence or heading ends the list
                if len(cur) > len(best):
                    best = cur
                cur, gap = [], 0
    if len(cur) > len(best):
        best = cur
    return merge_split_names(best) if len(best) >= min_len else []


def union_names(recs, soup):
    """Adds names found by parse_runs that the first reader missed (as name-only records)."""
    names = parse_runs(soup)
    if len(names) < max(8, int(len(recs) * 1.1)):
        return recs, 0
    known = {norm_name(r["Company Name"]) for r in recs}
    add = 0
    out = list(recs)
    for n in names:
        k = norm_name(n)
        if not k or k in known:
            continue
        known.add(k)
        rec = new_record(); rec["detail_url"] = ""; rec["Company Name"] = n
        out.append(rec); add += 1
    return out, add


# ---------------------------------------------------------------- page level
def _soup(html):
    return BeautifulSoup(html, "lxml")


def own_domain(url):
    return root_domain(domain_of(url) or "")


def _strip_filters(soup):
    """Remove search-filter menus (Halls, Countries, Product groups ... with '#' links) so they are not read as companies."""
    for el in list(soup.find_all(True)):
        if el.decomposed if hasattr(el, "decomposed") else False:
            continue
        own = " ".join(el.get("class", [])) + " " + (el.get("id") or "")
        if re.search(r"filter|facet", own, re.I) and el.name in ("ul", "ol", "div", "li", "section", "form", "aside"):
            el.decompose()
    for a in list(soup.find_all("a", attrs={"data-value": True})):
        if (a.get("href") or "").strip() in ("#", "") and not a.decomposed if hasattr(a, "decomposed") else False:
            a.decompose()
    return soup


def _parse_list_normal(html, base_url, profile=None):
    """The readers in order: profile, table, logos, cards, plain names."""
    soup = _soup(html)
    own = own_domain(base_url)
    if profile and profile.get("card"):
        items = soup.select(profile["card"])
        recs = [r for r in (record_from_block(el, base_url, own) for el in items) if r["Company Name"]]
        return recs, "profile"
    tables = parse_tables(soup, base_url, own)
    cards = parse_cards(soup, base_url, own)
    if len(tables) >= len(cards) and tables:
        return tables, "table"
    plain = parse_plain(_strip_filters(_soup(html)), base_url, own)
    soup2 = _strip_filters(_soup(html))
    logos = parse_logos(soup2, base_url, own)
    if logos and len(logos) >= max(len(cards), len(plain), len(tables)):
        return logos, "logos"
    if cards and all(not any(v for k, v in r.items() if k not in ("Company Name", "detail_url")) for r in cards):
        if len(parse_runs(soup2)) >= 3 * len(cards):          # name-only blocks that are really big columns
            cards = []
    rich = cards and sum(1 for r in cards if any(v for k, v in r.items() if k not in ("Company Name", "detail_url"))) >= 0.5 * len(cards)
    if cards and (len(cards) >= len(plain) / 2 or (rich and len(cards) >= 8)):
        sig = card_sig(html)                        # the same kind of block in other parts of the page
        if sig and sig[1]:                          # (a list split by letter: A block, B block ...)
            every = parse_cards_sig(html, base_url, sig)
            if len(every) > len(cards):
                return every, "cards (all blocks)"
        names = parse_runs(soup2)                   # no class to match: other blocks of names in the same list?
        grp = find_card_group(soup2)
        one_line = grp and sum(1 for g in grp if len(block_lines(g)) <= 1) >= 0.8 * len(grp)   # card = just a name
        if one_line and len(cards) >= 8 and len(names) >= 1.5 * len(cards):
            have = {norm_name(n) for n in names}
            if sum(1 for r in cards if norm_name(r["Company Name"]) in have) >= 0.8 * len(cards):
                joined, added = union_names(cards, soup2)
                if added:
                    return joined, "cards (joined blocks)"
        return cards, "cards"
    joined, added = union_names(plain, soup2)
    if joined:
        return joined, "list of names" + (" (joined blocks)" if added else "")
    return [], "none"


YT_RE = re.compile(r"(?:https?:)?//(?:www\.|m\.)?(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?(?:[^\"' <>]*&)?v=|v/|shorts/|live/)|youtu\.be/)([\w-]{6,15})", re.I)
VIMEO_RE = re.compile(r"(?:https?:)?//(?:player\.)?vimeo\.com/(?:video/)?(\d{5,12})", re.I)


def norm_video(text):
    """A YouTube / Vimeo address found in any text -> the normal watch address, or ''."""
    m = YT_RE.search(text or "")
    if m:
        return "https://www.youtube.com/watch?v=" + m.group(1)
    m = VIMEO_RE.search(text or "")
    if m:
        return "https://vimeo.com/" + m.group(1)
    return ""


def collect_videos(soup):
    """Videos shown as players (iframe), pop-up links or data- attributes: [(caption, watch-url)]."""
    out, seen = [], set()
    for el in soup.find_all(True):
        if el.name in ("script", "style", "meta", "link"):
            continue
        for attr, val in list(el.attrs.items()):
            if isinstance(val, list):
                val = " ".join(val)
            if attr in ("class", "id", "style") or not isinstance(val, str) or len(val) > 600:
                continue
            url = norm_video(val)
            if not url or url in seen:
                continue
            seen.add(url)
            cap = clean(el.get("title") or el.get("aria-label") or el.get("data-caption") or "")
            if not cap or cap.lower() in ("youtube video player", "video player", "video"):
                holder = el.find_parent(["figure", "div", "li", "article"])
                fc = None
                hops = 0
                while holder is not None and hops < 3 and fc is None:
                    fc = holder.find(["figcaption", "p", "h5", "h6", "span"], string=True) \
                        if holder.find(["figcaption"]) is None else holder.find("figcaption")
                    holder = holder.parent if fc is None else holder
                    hops += 1
                cap = clean(fc.get_text(" ")) if fc is not None else ""
            out.append((cap[:120], url))
    return out


VIEW_WORDS = re.compile(r"^\s*(view|preview|open|read|see|watch online)\b", re.I)
DOWNLOAD_WORDS = re.compile(r"download", re.I)


def keep_download_links(links):
    """Where a file has both a 'View' and a 'Download' link, keep only 'Download'."""
    def label(L):
        a = L["a"]
        return " ".join([L["text"], a.get("title") or "", a.get("aria-label") or "", " ".join(a.get("class", []))])
    groups = {}
    for L in links:
        holder = L["a"].parent
        for _ in range(2):                       # same card / row / list item
            if holder is not None and holder.parent is not None and holder.name in ("span", "div", "p") and \
                    len([x for x in holder.parent.find_all("a", href=True)]) <= 4:
                holder = holder.parent
        groups.setdefault(id(holder), []).append(L)
    out = []
    for grp in groups.values():
        has_dl = any(DOWNLOAD_WORDS.search(label(L)) for L in grp)
        for L in grp:
            if has_dl and not DOWNLOAD_WORDS.search(label(L)) and VIEW_WORDS.search(L["text"] or label(L)):
                continue
            out.append(L)
    seen, res = set(), []
    for L in out:                                # same address twice (view and download) -> once
        if L["url"] not in seen:
            seen.add(L["url"]); res.append(L)
    return res


def _tab_panes(soup):
    """Tabs like 'About the company | Products | Brochure': returns [(label, pane_element, button_element)]."""
    out = []
    for b in soup.find_all(True):
        tgt = b.get("data-bs-target") or b.get("data-target") or ""
        if not tgt and b.name == "a" and (b.get("href") or "").startswith("#") and \
                (b.get("role") == "tab" or b.get("data-bs-toggle") or b.get("data-toggle")):
            tgt = b["href"]
        if not tgt.startswith("#") or len(tgt) < 2:
            continue
        if not (b.get("role") == "tab" or b.get("data-bs-toggle") in ("tab", "pill") or b.get("data-toggle") in ("tab", "pill")):
            continue
        pane = soup.find(id=tgt[1:])
        label = clean(b.get_text(" "))
        if pane is not None and label:
            out.append((label, pane, b))
    return out


SECTION_HEAD = re.compile(r"^(product groups?|products?(?: (?:range|list|groups?|categories))?|product categories|contact information|"
                          r"contact details|contact|exhibition|exhibitions)$", re.I)
PHONE_LINE = re.compile(r"^\+?[\d][\d ()./\-]{5,}$")


def _heading_like(el):
    if not isinstance(el, Tag):
        return False
    if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return True
    cls = " ".join(el.get("class", []))
    txt = clean(el.get_text(" "))
    return bool(re.search(r"title|heading|headline", cls, re.I)) and 0 < len(txt) <= 40 and not el.find(["ul", "ol", "p"])


def _section_content(h):
    """Elements that follow a section heading, up to the next heading."""
    node = h
    while node.parent is not None and node.next_sibling is None or (
            node.parent is not None and not any(isinstance(x, Tag) for x in node.next_siblings)):
        node = node.parent
        if node.name in ("body", "html", "[document]"):
            return []
    out = []
    for sib in node.next_siblings:
        if not isinstance(sib, Tag):
            continue
        if _heading_like(sib) or sib.find(lambda t: _heading_like(t)):
            break
        out.append(sib)
    return out


def _apply_sections(soup, rec, base_url, own):
    """Pages that show 'Product Groups', 'Contact Information' ... as headings with a box below."""
    for h in soup.find_all(_heading_like):
        title = clean(h.get_text(" ")).rstrip(":")
        if not SECTION_HEAD.match(title):
            continue
        blocks = _section_content(h)
        if not blocks:
            continue
        low = title.lower()
        if low.startswith("product"):
            items = []
            for b in blocks:
                lis = b.find_all("li")
                parts = [clean(x.get_text(" ")) for x in lis] if lis else [clean(x) for x in block_lines(b) if x != END_MARK]
                items += [re.sub(r"^[-–•·*]\s*", "", p) for p in parts]
            items = [i for i in items if i and not label_target(i) and not SECTION_HEAD.match(i)]
            if items:
                rec["Product"] = list(dict.fromkeys(items))
        elif low.startswith("contact"):
            lines, links = [], []
            for b in blocks:
                for br in b.find_all("br"):
                    br.replace_with("\n")
                lines += [clean(x) for x in b.get_text("\n").split("\n") if clean(x)]
                links += classify_links(b, base_url, own)
            for L in links:
                if L["kind"] == "email" and not rec["Email"]:
                    rec["Email"] = re.sub(r"^mailto:", "", L["url"], flags=re.I)
                elif L["kind"] == "external" and not rec["Website"]:
                    rec["Website"] = L["url"]
            addr = []
            for ln in lines:
                if EMAIL_RE.fullmatch(ln):
                    rec["Email"] = rec["Email"] or ln
                elif re.fullmatch(r"(https?://|www\.)\S+", ln, re.I):
                    rec["Website"] = rec["Website"] or fix_url(ln)
                elif PHONE_LINE.match(ln):
                    item = "Phone: " + ln
                    if item not in rec["extra"]:
                        rec["extra"].append(item)
                elif re.fullmatch(r"[+\-–\s]*", ln):
                    continue
                else:
                    addr.append(ln)
            if addr and rec["Company Name"] == "" or addr:
                if addr and norm_name(addr[0]) == norm_name(rec.get("Company Name", "")):      # first line = the company name
                    addr = addr[1:]
                if addr and not rec["Address / Corporate Address"]:
                    rec["Address / Corporate Address"] = ", ".join(addr)
        elif low.startswith("exhibition"):
            txt = clean(" ".join(clean(b.get_text(" ")) for b in blocks))
            m = re.search(r"exhibiting at\s+(.+?)\.?$", txt, re.I)
            val = clean(m.group(1)) if m else txt
            if val and not re.fullmatch(r"[-–—]*", val):
                rec["extra"].append("Exhibition: " + val)


def parse_detail(html, base_url, expected_name=""):
    """Read a company's own page (all details of one company)."""
    soup = _soup(html)
    own = own_domain(base_url)
    for t in soup.find_all(["nav", "footer", "header", "aside"]):
        t.decompose()
    for t in soup.select(".modal, [role=dialog]"):
        t.decompose()
    videos = collect_videos(soup)          # before iframes are removed: players are videos
    for t in soup.find_all(list(SKIP_TAGS - {"head"})):
        t.decompose()
    tab_pairs, tab_links = [], []
    for label, pane, btn in _tab_panes(soup):        # tab pages: label -> content of its pane
        tgt = label_target(label)
        if tgt in SINGLE or tgt in MULTI:
            if tgt in ("Brochures", "Press Release", "Video"):
                items = []                      # build the "name: link" text now, before the pane is removed
                links = classify_links(pane, base_url, own)
                if tgt in ("Brochures", "Press Release"):
                    links = keep_download_links(links)
                for L in links:
                    if L["kind"] in ("email", "tel", "social"):
                        continue
                    items.append(f"{link_name(L)}: {L['url']}" if L["kind"] != "video" or L["text"] else L["url"])
                tab_links.append((tgt, items))
            else:
                items = []
                for fc in pane.find_all(["figcaption"]):
                    items.append(clean(fc.get_text(" ")))
                for c in pane.select("[data-caption]"):
                    items.append(clean(c.get("data-caption", "")))
                if tgt == "Product" and items:
                    value = "\n".join(dict.fromkeys(i for i in items if i))
                else:
                    value = "\n".join(x for x in block_lines(pane) if x != END_MARK)
                tab_pairs.append((tgt, label, value))
        pane.decompose()
        btn.decompose()
    badges = [clean(b.get_text(" ")) for b in soup.select(".badge, .tag, .label-tag") if clean(b.get_text(" "))]
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    rec = record_from_block(main, base_url, own, want_name=False)
    fill_from_pairs(rec, tab_pairs)
    for cap, url in videos:                 # players first: normal watch address + caption
        vid = url.rsplit("=", 1)[-1].rsplit("/", 1)[-1]
        if not any(vid in x for x in rec["Video"]):
            rec["Video"].append(f"{cap}: {url}" if cap else url)
    for tgt, items in tab_links:
        for item in items:
            nv = norm_video(item)
            if nv and any(nv.rsplit("=", 1)[-1].rsplit("/", 1)[-1] in x for x in rec[tgt]):
                continue                    # same video already added from its player
            if item not in rec[tgt]:
                rec[tgt].append(item)
    for b in dict.fromkeys(badges):
        item = "Tag: " + b
        if item not in rec["extra"]:
            rec["extra"].append(item)
    h1 = soup.find("h1")
    name = clean(h1.get_text(" ")) if h1 else ""
    if name and not label_target(name):
        rec["Company Name"] = rec["Company Name"] or name
    if not rec["Company Name"]:
        rec["Company Name"] = expected_name
    _apply_sections(soup, rec, base_url, own)
    fixed = []
    for e in rec["extra"]:
        m = re.match(r"^Booth:\s*[-–—]*\s*;\s*EXHIBITION\s*;\s*(?:This supplier is exhibiting at\s+)?(.+?)\.?$", e, re.I)
        if m:
            e = "Exhibition: " + clean(m.group(1))
        if re.fullmatch(r"[^:]*:\s*[-–—]*\s*", e):
            continue
        fixed.append(e)
    rec["extra"] = list(dict.fromkeys(fixed))
    rec["detail_url"] = ""
    return rec


def merge_detail(rec, det):
    """Add what the company page gave to the list record (fill empty, add new items)."""
    for f in SINGLE:
        if f != "Company Name" and det.get(f) and not rec.get(f):
            rec[f] = det[f]
    for f in MULTI:
        for it in det.get(f, []):
            if it not in rec[f]:
                rec[f].append(it)
    for c in det.get("contacts", []):
        if c not in rec["contacts"]:
            rec["contacts"].append(c)
    for e in det.get("extra", []):
        if e not in rec["extra"]:
            rec["extra"].append(e)
    return rec


def drop_placeholder_logos(records):
    """A logo that many companies share (or that looks like a placeholder) is not a company logo."""
    cnt = {}
    for r in records:
        u = r.get("Logo / Image")
        if u:
            cnt[u] = cnt.get(u, 0) + 1
    for r in records:
        u = r.get("Logo / Image")
        if u and (cnt[u] >= 3 or PLACEHOLDER_RE.search(u)):
            r["Logo / Image"] = ""
    return records


def dedupe_names(records):
    seen, out = set(), []
    for r in records:
        k = (r["Company Name"].lower(), r.get("Website", "").lower(), tuple(r.get("extra", [])))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def has_js_pagination(html):
    """True when the page has page numbers / next / load-more controls that run by JavaScript (no real link)."""
    sp = _soup(html)
    for a in sp.find_all(["a", "button"]):
        cls = " ".join(a.get("class", [])).lower()
        txt = clean(a.get_text(" ")).lower()
        href = (a.get("href") or "").strip().lower()
        dead = (not href) or href.startswith(("#", "javascript"))
        if "pagination" in cls and dead and (a.get("data-page") or re.fullmatch(r"\d{1,4}|»|›|next|last", txt)):
            return True
        if dead and re.fullmatch(r"(load more|show more|view more|next|next page|»|›)", txt):
            return True
        if a.get("data-page") and dead:
            return True
    return False


def iframe_urls(html, base_url):
    """Addresses of frames inside a page (the list is sometimes in a frame)."""
    out = []
    for f in _soup(html).find_all(["iframe", "frame"], src=True):
        src = (f.get("src") or "").strip()
        if not src or src.startswith(("about:", "javascript", "data:")):
            continue
        if re.search(r"youtube|vimeo|google\.com/maps|facebook|twitter|doubleclick|googletagmanager", src, re.I):
            continue
        out.append(urljoin(base_url, src))
    return out


# ---------------------------------------------------------------- pagination
NEXT_TEXT = re.compile(r"^(next|next page|next »|older|more|»|›|>|>>|→|forward|load more|show more|view more)$", re.I)


def find_next_url(html, base_url):
    """Link to the next list page, or ''."""
    soup = _soup(html)
    a = soup.find("a", attrs={"rel": re.compile(r"\bnext\b", re.I)}, href=True)
    if a:
        return urljoin(base_url, a["href"])
    link = soup.find("link", attrs={"rel": "next"}, href=True)
    if link:
        return urljoin(base_url, link["href"])
    for a in soup.find_all("a", href=True):
        txt = clean(a.get_text(" ")) or a.get("aria-label", "") or a.get("title", "")
        cls = " ".join(a.get("class", [])) + " " + " ".join(a.parent.get("class", []) if a.parent else [])
        if (NEXT_TEXT.match(clean(txt)) or re.search(r"\bnext\b", cls, re.I)) and not a["href"].startswith(("#", "javascript")):
            if "disabled" in cls.lower():
                continue
            return urljoin(base_url, a["href"])
    return ""


def page_number_urls(html, base_url):
    """Links of a numbered pager (1 2 3 ...), in order. The current page is often not a link."""
    soup = _soup(html)
    out = []
    for parent in soup.find_all(["ul", "ol", "div", "nav", "span", "p", "td"]):
        if _in_filter_name(parent):
            continue
        links = parent.find_all("a", href=True)
        nums = [a for a in links if re.fullmatch(r"\d{1,4}", clean(a.get_text(" ")))
                and not a["href"].startswith(("#", "javascript"))]
        if len(nums) >= 2 and len(nums) >= 0.5 * len(links) and len(links) <= 40:
            for a in nums:
                out.append(urljoin(base_url, a["href"]))
            break
    seen, res = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); res.append(u)
    return res


def _in_filter_name(el):
    """An A-Z / filter bar, not a pager."""
    return bool(re.search(r"alphabet|\baz\b|a-z|filter|facet", " ".join(el.get("class", [])) + " " + (el.get("id") or ""), re.I))


# ---------------------------------------------------------------- year and site name
def detect_year(html, url, now_year=None):
    import datetime
    now_year = now_year or datetime.date.today().year
    ok = lambda y: 2000 <= int(y) <= now_year + 3
    soup = _soup(html)
    cands = []
    for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", url or ""):
        if ok(y):
            cands.append((12, y))
    title = clean(soup.title.get_text(" ")) if soup.title else ""
    for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", title):
        if ok(y):
            cands.append((4, y))
    for tag in soup.find_all(["h1", "h2"])[:6]:
        for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", clean(tag.get_text(" "))):
            if ok(y):
                cands.append((3, y))
    for m in soup.find_all("meta"):
        c = (m.get("content") or "") if (m.get("property") or m.get("name") or "").lower() in ("og:title", "og:description", "description") else ""
        for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", c):
            if ok(y):
                cands.append((3, y))
    body = soup.get_text(" ")
    body = re.sub(r"(©|copyright)[^.\n]{0,40}", " ", body, flags=re.I)
    freq = {}
    for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", body[:20000]):
        if ok(y):
            freq[y] = freq.get(y, 0) + 1
    if freq:
        y = max(freq, key=lambda k: (freq[k], k))
        cands.append((1, y))
    if not cands:
        return ""
    score = {}
    for w, y in cands:
        score[y] = score.get(y, 0) + w
    return max(score, key=lambda k: (score[k], k))


def detect_site_name(html, url):
    soup = _soup(html)
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and clean(og.get("content", "")):
        name = clean(og["content"])
    else:
        t = clean(soup.title.get_text(" ")) if soup.title else ""
        parts = [p.strip() for p in re.split(r"\s[|\-–—•:]\s", t) if p.strip()]
        name = ""
        host = root_domain(domain_of(url) or "").split(".")[0].lower()
        for p in parts:
            if host and host.replace("-", "") in re.sub(r"[^a-z0-9]", "", p.lower()):
                name = p
                break
        if not name and parts:
            name = parts[-1] if len(parts) > 1 else parts[0]
        if not name:
            name = host.title()
    name = re.sub(r"(?<!\d)20\d\d(?!\d)", "", name)
    name = re.sub(r"\b(exhibitor list|exhibitors?|exhibitor directory|list of exhibitors)\b", "", name, flags=re.I)
    name = clean(re.sub(r"[\[\]\\/*?:]", " ", name)).strip(" -|–—")
    return name[:80] or root_domain(domain_of(url) or "site")


def css_path(el):
    """A CSS path to an element (used to click tiles on JavaScript pages)."""
    parts = []
    while el is not None and isinstance(el, Tag) and el.name not in ("[document]", "html"):
        sibs = [s for s in el.parent.find_all(el.name, recursive=False)] if el.parent else [el]
        idx = sibs.index(el) + 1
        parts.append(f"{el.name}:nth-of-type({idx})")
        el = el.parent
    return "html > " + " > ".join(reversed(parts))


TOTAL_RES = [re.compile(p, re.I) for p in (
    r"(?:total|showing|found|displaying)[^0-9]{0,40}?(\d[\d,]{1,6})\s*(?:exhibitors?|companies|results?|records?|entries|brands?)",
    r"(\d[\d,]{1,6})\s*(?:exhibitors?|companies|results?\s+found|records?\s+found)",
    r"(?:exhibitors?|companies)\s*[:(\-]\s*(\d[\d,]{1,6})\b",
    r"of\s+(\d[\d,]{1,6})\s*(?:exhibitors?|companies|results?|entries)")]


def stated_total(html):
    """A total the page itself states ('549 exhibitors', 'Showing 1-24 of 549 results'), or 0."""
    soup = _soup(html)
    for t in soup.find_all(list(SKIP_TAGS)):
        t.decompose()
    text = clean(soup.get_text(" "))
    vals = []
    for rx in TOTAL_RES:
        for m in rx.finditer(text):
            v = int(m.group(1).replace(",", ""))
            if 10 <= v <= 50000:
                vals.append(v)
    return max(vals) if vals else 0


def letter_bar(html, base_url=""):
    """An A-Z (0-9) bar: >=15 single-character links/buttons in one block.
    Returns dict(letters=[...], urls=[real links]) or None."""
    soup = _soup(html)
    groups = {}
    for el in soup.find_all(["a", "button", "li", "span"]):
        if _in_nav(el) and False:
            continue
        t = clean(el.get_text(" "))
        if not re.fullmatch(r"[A-Za-z0-9]", t) or el.find(["a", "button", "li"]):
            continue
        box = el.find_parent(["ul", "ol", "div", "nav", "p", "td"]) or soup
        groups.setdefault(id(box), []).append(el)
    best = max(groups.values(), key=len, default=[])
    if len(best) < 15:
        return None
    urls = []
    for el in best:
        a = el if el.name == "a" else (el.find_parent("a") or el.find("a"))
        h = (a.get("href") or "").strip() if a else ""
        if h and not h.startswith("#") and not h.lower().startswith("javascript"):
            u = urljoin(base_url, h)
            if u not in urls:
                urls.append(u)
    return {"letters": [clean(el.get_text(" ")) for el in best], "urls": urls}


def first_letters(records):
    return {clean(r["Company Name"])[:1].upper() for r in records if clean(r["Company Name"])}


def parse_list_lenient(html, base_url, profile=None, sig=None):
    """For a letter page that may hold only 1-7 companies (Q, X ...): normal reader, else any run of names."""
    if sig:
        recs = parse_cards_sig(html, base_url, sig)
        if recs:
            return recs
    recs, how = parse_list(html, base_url, profile)
    if recs:
        return recs
    out = []
    for n in parse_runs(_soup(html), min_len=1):
        rec = new_record(); rec["detail_url"] = ""; rec["Company Name"] = n
        out.append(rec)
    return out


def _is_country_list(recs):
    """A list that is only country names is a filter menu (Countries: Australia, Austria ...), not companies."""
    names = [clean(r["Company Name"]).lower() for r in recs]
    return len(names) >= 3 and sum(1 for n in names if n in COUNTRIES or n.replace("sar of china", "").strip() in COUNTRIES) >= 0.7 * len(names)


def _drop_country_filter(recs, how):
    if recs and _is_country_list(recs):
        return [], "none"
    return recs, how


def parse_list(html, base_url, profile=None):
    """Read a list page. Returns (records, how) where how names the rule that worked."""
    recs, how = _parse_list_normal(html, base_url, profile)
    if how in ("profile", "table", "logos"):
        return recs, how
    recs, how = _drop_country_filter(recs, how)
    try:
        ent = parse_entries(_soup(html), base_url, own_domain(base_url), recs, html)
    except Exception:
        ent = []
    if ent and not _is_country_list(ent):
        return ent, "entries"
    return recs, how
