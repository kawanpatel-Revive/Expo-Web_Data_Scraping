"""Company table with a title row / no heading row, next to a list of year links (asiapharma.org style)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from expo import extract as ex

ROWS = [("3F INDUSTRIES LTD", "India"), ("AARTI API LTD", "Bangladesh"), ("ACCHROM TECHNOLOGIES CO LTD", "China")] + \
       [(f"FIRM {i} PVT LTD", "India") for i in range(40)]
YEARS = "".join(f"<li><a href='l{y}.php'>{y} Exhibitors List</a></li>" for y in range(2026, 2016, -1))


def page(table):
    return (f"<html><body><ul class='years'>{YEARS}</ul><h2>Exhibitor List 2025</h2>{table}</body></html>")


def body(rows):
    return "".join(f"<tr><td>{n}</td><td>{c}</td></tr>" for n, c in rows)


def check(html):
    recs, how = ex.parse_list(html, "https://asiapharma.org/ape/exhibitor_list_2025.php")
    assert len(recs) == len(ROWS), (how, len(recs), [r["Company Name"] for r in recs[:3]])
    assert recs[0]["Company Name"] == "3F INDUSTRIES LTD" and recs[1]["Country"] == "Bangladesh"


def test_title_row_then_heading_row():
    check(page("<table><tr><th colspan='2'>Exhibitor List 2025</th></tr><tr><th>COMPANY NAME</th><th>COUNTRY</th></tr>" + body(ROWS) + "</table>"))


def test_table_without_heading_row():
    check(page("<table>" + body(ROWS) + "</table>"))


def test_normal_heading_row():
    check(page("<table><thead><tr><th>COMPANY NAME</th><th>COUNTRY</th></tr></thead><tbody>" + body(ROWS) + "</tbody></table>"))
