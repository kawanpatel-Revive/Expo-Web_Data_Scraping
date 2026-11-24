"""The smarter E list as the browser built it (cards = links, stand number before the name, endless scrolling)."""
import os, sys, re, html as H, threading, http.server, socketserver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from expo import extract as ex, webread

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "smartere_de_rendered.html")


def test_every_card_read_with_right_name():
    h = open(FIX, encoding="utf-8", errors="replace").read()
    names = [H.unescape(n) for n in re.findall(r'<span class="h1">([^<]*)</span>', h)]
    recs, how = ex.parse_list(h, "https://www.thesmartere.de/exhibitorlist")
    assert [r["Company Name"] for r in recs] == names
    by = {r["Company Name"]: r for r in recs}
    assert "Stand / Hall: B5.430, B5.530" in by["ABB AG Stotz-Kontakt"]["extra"]
    assert "Exhibition: ees Europe" in by["3A Technology Co., Ltd"]["extra"]
    assert by["3A Technology Co., Ltd"]["Country"] == "China"
    assert by["A PROFIL SAN. VE TIC. A.S."]["Country"]            # Türkiye
    assert by["8.2 Group e.V."]["detail_url"]


def test_endless_scroll_is_followed_until_the_end():
    TOTAL = 150

    def page(n):
        cards = "".join(f"<a class='teaser' href='/x/{i}'><span class='h2'>A1.{i}</span><span class='h1'>Firm {i} GmbH</span>"
                        f"<p>EM-Power Europe</p><p>Germany</p></a>" for i in range(n))
        js = ("var n=%d,T=%d;function more(){setTimeout(function(){var h='';for(var i=n;i<Math.min(n+24,T);i++)"
              "h+='<a class=\"teaser\" href=\"/x/'+i+'\"><span class=\"h2\">A1.'+i+'</span><span class=\"h1\">Firm '+i+' GmbH</span><p>EM-Power Europe</p><p>Germany</p></a>';"
              "document.getElementById('r').insertAdjacentHTML('beforeend',h);n=Math.min(n+24,T);watch();},2500);}"
              "function watch(){if(n>=T)return;var t=document.querySelectorAll('a.teaser');var last=t[t.length-18];"
              "var o=new IntersectionObserver(function(e){if(e[0].isIntersecting){o.disconnect();more();}});o.observe(last);}"
              "window.onload=watch;") % (n, TOTAL)
        return f"<html><body><style>a.teaser{{display:block;height:120px}}</style><div id='r'>{cards}</div><script>{js}</script></body></html>"

    class Hd(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = (b"<html><body><h2>Company</h2><p>x@y.de</p></body></html>" if "/x/" in self.path else page(24).encode())
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def log_message(self, *a): pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), Hd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r = webread.read_with_browser(f"http://127.0.0.1:{srv.server_address[1]}/list", fetch_details=False)
    srv.shutdown()
    assert len(r["records"]) == TOTAL, (len(r["records"]), r["notes"])
