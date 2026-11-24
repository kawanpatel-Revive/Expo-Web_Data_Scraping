"""JavaScript list (cards drawn after load) next to a Countries filter; company page with sections."""
import os, sys, threading, http.server, socketserver, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from expo import extract as ex, webread

N = 25
FILTER = "<ul class='countries'>" + "".join(f"<li><a href='#'>{c}</a></li>" for c in
                                            ["Australia", "Austria", "Belgium", "Bulgaria", "Canada", "China", "Croatia"]) + "</ul>"


def list_page():
    js = ("var d=[];for(var i=0;i<%d;i++){d.push('<a class=\"item\" href=\"/exhibitor/'+i+'\"><span>B5.'+(100+i)+'</span>"
          "<div class=\"nm\">Solar Firm '+i+' GmbH</div><div>Intersolar Europe</div><div>Germany</div></a>');}"
          "setTimeout(function(){document.getElementById('res').innerHTML=d.join('');},300);") % N
    return f"<html><body><div class='filter'>{FILTER}</div><div id='res' class='results'></div><script>{js}</script></body></html>"


def detail_page(i):
    return (f"<html><body><a href='/exhibitorlist'>To Exhibitor List</a><h5>Product Groups</h5><div class='box'><ul><li>Inverters {i}</li>"
            f"<li>Storage {i}</li></ul></div><h5>Contact Information</h5><div class='box'><div>+</div><div>info{i}@firm{i}.com</div>"
            f"<div><a href='https://www.firm{i}.com'>https://www.firm{i}.com</a></div><div><b>Solar Firm {i} GmbH</b><br>Main Street {i}<br>80331 Munich, Germany</div></div>"
            f"<h2>Solar Firm {i} GmbH</h2><div>BOOTH</div><div>-</div><div>EXHIBITION</div><div>This supplier is exhibiting at ees Europe.</div></body></html>")


def test_country_filter_is_not_a_company_list():
    recs, how = ex.parse_list("<html><body>" + FILTER + "</body></html>", "https://www.thesmartere.de/exhibitorlist")
    assert recs == []


def test_detail_sections():
    d = ex.parse_detail(detail_page(3), "https://www.thesmartere.de/exhibitor/3", "Solar Firm 3 GmbH")
    assert d["Product"] == ["Inverters 3", "Storage 3"]
    assert d["Email"] == "info3@firm3.com" and d["Website"] == "https://www.firm3.com"
    assert d["Address / Corporate Address"] == "Main Street 3, 80331 Munich, Germany"
    assert "Exhibition: ees Europe" in d["extra"]


def test_js_list_read_by_browser_automatically():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            m = re.search(r"/exhibitor/(\d+)", self.path)
            b = (detail_page(int(m.group(1))) if m else list_page()).encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def log_message(self, *a): pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r = webread.read_link(f"http://127.0.0.1:{srv.server_address[1]}/exhibitorlist")
    srv.shutdown()
    recs = r["records"]
    assert len(recs) == N, (len(recs), r["notes"])
    assert recs[0]["Country"] == "Germany" and recs[0]["Product"] and recs[0]["Email"]


def test_real_static_page_has_no_companies_only_filters():
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "smartere_de_static.html"),
                encoding="utf-8", errors="replace").read()
    recs, how = ex.parse_list(html, "https://www.thesmartere.de/exhibitorlist")
    assert recs == [], [r["Company Name"] for r in recs[:10]]
