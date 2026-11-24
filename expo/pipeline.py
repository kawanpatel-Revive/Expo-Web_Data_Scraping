"""Glue between the readers and the Google Sheet: save records into a website's year tab."""
import datetime, re
from .tabio import build_state, save_state
from .matcher import summary_line

SAVE_EVERY = 25


def clean_year(y):
    y = re.sub(r"[\[\]\\/*?:]", " ", str(y or "")).strip()
    return y[:30]


def clean_sheet_name(n):
    n = re.sub(r"[\[\]\\/*?:]", " ", str(n or ""))
    return re.sub(r"\s+", " ", n).strip()[:100]


def save_records(client, sheet_name, year, records, source, progress=None):
    """Creates the Sheet / tab when missing, merges the records, saves in small batches.
    Returns (summary_line, stats, sheet_url)."""
    progress = progress or (lambda *a: None)
    sheet_name, year = clean_sheet_name(sheet_name), clean_year(year)
    if not sheet_name or not year:
        raise ValueError("Website name and year are needed.")
    sh = client.ensure_sheet(sheet_name)
    client.ensure_tab(sh["id"], year)
    header, rows = client.read_tab(sh["id"], year)
    state, colmap, width = build_state(header, rows)
    now = datetime.datetime.now().strftime("%d-%b-%Y %H:%M")
    for n, rec in enumerate(records, 1):
        state.merge(rec, source, now)
        if n % SAVE_EVERY == 0:
            save_state(client, sh["id"], year, state, colmap, width)
            progress(n, len(records))
    save_state(client, sh["id"], year, state, colmap, width)
    progress(len(records), len(records))
    st = state.stats()
    return summary_line(st), st, sh["url"]
