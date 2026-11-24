"""Small helpers: cleaning and normalising text, websites, emails, phones, company names."""
import re
from urllib.parse import urlparse

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "ymail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "rediffmail.com", "aol.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "zoho.com", "gmx.com",
    "mail.com", "yandex.com", "qq.com", "163.com", "163.net", "sina.com",
}
SOCIAL = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "youtu.be", "wa.me", "whatsapp.com", "t.me", "pinterest.com", "tiktok.com",
}
LEGAL = {
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "llp", "llc", "gmbh", "ag", "co",
    "corp", "corporation", "company", "sa", "srl", "bv", "plc", "pte", "pty", "kg", "oy", "ab",
    "as", "spa", "sarl", "nv", "kk", "the", "and", "&",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w/])(?:\+|00)?\d[\d\s().\-]{7,17}\d(?![\w])")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)


def clean(s):
    """Collapse whitespace, strip. Always returns str."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = s.replace(" ", " ").replace("​", "")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def clean_multiline(s):
    s = (s or "").replace(" ", " ")
    lines = [clean(x) for x in re.split(r"\n+", s)]
    return "\n".join(x for x in lines if x)


def domain_of(url_or_email):
    """example.com from a url, a www. address or an email. '' if none."""
    s = clean(url_or_email).lower()
    if not s:
        return ""
    if "@" in s and "/" not in s:
        d = s.split("@")[-1]
    else:
        if not re.match(r"^[a-z][a-z0-9+.\-]*://", s):
            s = "http://" + s
        try:
            d = urlparse(s).netloc
        except ValueError:
            return ""
    d = d.split(":")[0].strip(".")
    if d.startswith("www."):
        d = d[4:]
    if "." not in d:
        return ""
    return d


def root_domain(d):
    """Keep it simple: last two labels (last three for co.in / com.au style)."""
    parts = d.split(".")
    if len(parts) <= 2:
        return d
    if parts[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def company_domain(url_or_email):
    """Domain that identifies a company, ignoring free mail and social sites."""
    d = domain_of(url_or_email)
    if not d:
        return ""
    r = root_domain(d)
    if r in FREE_MAIL or r in SOCIAL or d in FREE_MAIL or d in SOCIAL:
        return ""
    return r


def norm_url(u):
    """Comparable form of a link (no scheme, no www, no trailing slash, lower case)."""
    s = clean(u).lower()
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def norm_text(s):
    return re.sub(r"\s+", " ", clean(s).lower())


def digits(s):
    return re.sub(r"\D", "", s or "")


def phone_key(s):
    d = digits(s)
    if len(d) < 8:
        return ""
    return d[-9:]


def norm_name(s):
    """Company name for matching: lower case, no punctuation, no legal words."""
    s = clean(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\u0080-￿ ]+", " ", s)
    words = [w for w in s.split() if w not in LEGAL]
    return " ".join(words)


def fix_url(u, base=""):
    u = clean(u)
    if not u:
        return ""
    if u.lower().startswith("www."):
        return "https://" + u
    if not base and re.fullmatch(r"[\w\-]+(\.[\w\-]+)*\.[a-z]{2,}(/\S*)?", u, re.I) and "@" not in u:
        return "https://" + u
    if base and not re.match(r"^[a-z][a-z0-9+.\-]*:", u, re.I):
        from urllib.parse import urljoin
        return urljoin(base, u)
    return u


def short_source(src, n=70):
    src = clean(src)
    return src if len(src) <= n else src[: n - 1] + "…"
