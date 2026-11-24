"""a2zinc exhibitor list: names link to openURL.aspx?...HyperLinkURL=<company site>, booth link has MapItBooth=<stand>."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from expo import extract as ex


def test_redirect_link_gives_website_and_booth():
    rows = [("A&B Brush", "410", "https://www.abbrush.com"), ("Allied Universal Corp.", "733", "https://www.alliedu.com"),
            ("Aquabella Tile", "621", ""), ("AquaCal AutoPilot", "1201", "https://www.aquacal.com")]
    az = "".join(f"<li><a href='#'>{c}</a></li>" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    body = "".join(
        f"<li><a href='{'openURL.aspx?BoothID=%d&HyperLinkURL=%s&EventID=15' % (i, w) if w else '#'}'>{n}</a>"
        f"<a href='eventmap.aspx?MapID=26&MapItBoothID={i}&MapItBooth={b}'>{b}</a></li>" for i, (n, b, w) in enumerate(rows))
    html = f"<html><body><form><ul><li><a href='#'>All</a></li>{az}</ul><div>4 Exhibitors</div><div>{body}</div></form></body></html>"
    recs, how = ex.parse_list(html, "https://s36.a2zinc.net/clients/fspa/opss2027/public/exhibitors.aspx")
    assert [r["Company Name"] for r in recs] == [r[0] for r in rows]
    assert recs[0]["Website"] == "https://www.abbrush.com" and recs[0]["detail_url"] == ""
    assert "Stand / Hall: 410" in recs[0]["extra"] and recs[2]["Website"] == ""
