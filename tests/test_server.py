import json
import threading
import urllib.error
import urllib.request

import pytest

from agent_workbench import page, store
from agent_workbench.config import parse
from agent_workbench.server import make_server


@pytest.fixture
def running(cfg, report_cards):
    page.save_report(cfg, "rep", report_cards)
    page.build_page(cfg, "rep")
    srv = make_server(cfg, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    yield cfg, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url, data=None, headers=None, method=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_and_index_are_served(running):
    _, base = running
    code, body = call(base + "/rep.html")
    assert code == 200 and b"data-card" in body
    code, body = call(base + "/")
    assert code == 200 and b"rep.html" in body


def test_post_remark_appends_jsonl_and_reads_back(running):
    cfg, base = running
    code, body = call(
        base + "/api/remark",
        {"report": "rep", "card": "c2", "verdict": "reject", "comment": "wrong édition", "rev": "r"},
    )
    assert code == 200 and json.loads(body)["ok"]
    rows = store.read_jsonl(cfg.remarks_file)
    assert rows[-1]["comment"] == "wrong édition" and rows[-1]["verdict"] == "reject"
    code, body = call(base + "/api/remarks?report=rep")
    assert json.loads(body)["c2"]["verdict"] == "reject"


def test_bad_requests_are_refused(running):
    cfg, base = running
    assert call(base + "/api/remark", {"report": "rep", "card": "c1", "verdict": "maybe"})[0] == 400
    assert call(base + "/api/remark", {"report": "", "card": "c1"})[0] == 400
    assert (
        call(base + "/api/remark", {"report": "rep", "card": "c1"}, headers={"Content-Type": "text/plain"})[0]
        == 415
    )
    assert (
        call(
            base + "/api/remark", {"report": "rep", "card": "c1"}, headers={"Origin": "http://evil.example"}
        )[0]
        == 403
    )
    assert call(base + "/api/remark", {"x": "y" * 70000})[0] == 413
    assert call(base + "/api/nothing", {"a": 1})[0] == 404
    assert store.read_jsonl(cfg.remarks_file) == []


def test_host_header_must_be_loopback(running):
    _, base = running
    assert call(base + "/api/status", headers={"Host": "evil.example"})[0] == 403
    assert call(base + "/api/status", headers={"Host": "localhost:9"})[0] == 200


def test_path_traversal_is_confined(running):
    cfg, base = running
    (cfg.root / "secret.txt").write_text("secret")
    for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        code, body = call(base + p)
        assert b"secret" not in body


def test_status_and_news(running):
    cfg, base = running
    store.post_news(cfg.news_file, "hi", "question")
    assert json.loads(call(base + "/api/news")[1])[0]["text"] == "hi"
    assert json.loads(call(base + "/api/status")[1]) == {"open": 3, "reports": {"rep": 3}}


def test_non_loopback_needs_a_token(tmp_path):
    with pytest.raises(SystemExit, match="refusing"):
        make_server(parse({}, tmp_path), "0.0.0.0", 0)


def test_token_cookie_flow(tmp_path, report_cards):
    cfg = parse({"server": {"token": "s3cret"}}, tmp_path)
    page.save_report(cfg, "rep", report_cards)
    page.build_page(cfg, "rep")
    srv = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert call(base + "/rep.html")[0] == 403
        assert call(base + "/rep.html?k=wrong")[0] == 403
        assert call(base + "/rep.html", headers={"Cookie": "awbk=s3cret"})[0] == 403  # the cookie is a digest

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(base + "/rep.html?k=s3cret")
        cookie = e.value.headers["Set-Cookie"].split(";")[0]
        assert e.value.code == 302 and "s3cret" not in cookie
        assert call(base + "/rep.html", headers={"Cookie": cookie})[0] == 200
        assert (
            call(base + "/api/remark", {"report": "rep", "card": "c1"}, headers={"Cookie": cookie})[0] == 200
        )
    finally:
        srv.shutdown()
        srv.server_close()


def test_base_remarks_are_merged(tmp_path, report_cards):
    base_f = tmp_path / "base.jsonl"
    store.append_jsonl(base_f, store.make_remark("rep", "c1", "reject", "from laptop"))
    cfg = parse({"server": {"base_remarks": str(base_f)}}, tmp_path)
    page.save_report(cfg, "rep", report_cards)
    srv = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        _, body = call(f"http://127.0.0.1:{srv.server_address[1]}/api/remarks?report=rep")
        assert json.loads(body)["c1"]["comment"] == "from laptop"
    finally:
        srv.shutdown()
        srv.server_close()


# -- hardening ------------------------------------------------------------------------------------------------
def raw(base, method, path, headers=None, body=None):
    """One request with exactly these headers (urllib normalises paths and lengths; http.client does not)."""
    import http.client

    host, port = base.removeprefix("http://").split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in {"Host": f"{host}:{port}", **(headers or {})}.items():
        c.putheader(k, v)
    c.endheaders(body)
    r = c.getresponse()
    out = r.status, dict(r.getheaders()), r.read()
    c.close()
    return out


def test_no_response_can_be_framed_and_pictures_are_inert(running):
    cfg, base = running
    (cfg.site_dir / "rep").mkdir(exist_ok=True)
    (cfg.site_dir / "rep" / "x.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'
    )
    (cfg.site_dir / "rep" / "x.png").write_bytes(b"\x89PNG")
    for path in ("/rep.html", "/", "/api/status", "/rep/x.png", "/rep/x.svg", "/nope.html"):
        _, h, _ = raw(base, "GET", path)
        assert "frame-ancestors 'none'" in h["Content-Security-Policy"], path
        assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff", path
        assert h["Referrer-Policy"] == "no-referrer", path
    # an SVG opened in its own tab is a document in the page's origin: it is served sandboxed, script off
    svg = raw(base, "GET", "/rep/x.svg")[1]["Content-Security-Policy"]
    assert "sandbox" in svg and "default-src 'none'" in svg
    assert "sandbox" not in raw(base, "GET", "/rep.html")[1]["Content-Security-Policy"]


def test_directories_are_not_listed(running):
    cfg, base = running
    (cfg.site_dir / "rep").mkdir(exist_ok=True)
    (cfg.site_dir / "rep" / "private-name.png").write_bytes(b"\x89PNG")
    code, _, body = raw(base, "GET", "/rep/")
    assert code == 404 and b"private-name" not in body
    assert raw(base, "GET", "/rep/private-name.png")[0] == 200


def test_post_refuses_cross_site_fetch_metadata_and_bad_lengths(running):
    cfg, base = running
    body = json.dumps({"report": "rep", "card": "c1", "verdict": "approve"}).encode()
    h = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    assert raw(base, "POST", "/api/remark", {**h, "Sec-Fetch-Site": "cross-site"}, body)[0] == 403
    assert raw(base, "POST", "/api/remark", {**h, "Sec-Fetch-Site": "same-site"}, body)[0] == 403
    assert raw(base, "POST", "/api/remark", {**h, "Content-Length": "-1"})[0] == 400
    assert raw(base, "POST", "/api/remark", {**h, "Content-Length": "abc"})[0] == 400
    assert store.read_jsonl(cfg.remarks_file) == []
    assert raw(base, "POST", "/api/remark", {**h, "Sec-Fetch-Site": "same-origin"}, body)[0] == 200
    assert len(store.read_jsonl(cfg.remarks_file)) == 1


def test_bad_host_says_how_to_allow_it(running):
    _, base = running
    code, _, body = raw(base, "GET", "/api/status", {"Host": "review.example:8765"})
    assert code == 403 and "allowed_hosts" in json.loads(body)["hint"]


@pytest.fixture
def with_token(tmp_path, report_cards):
    cfg = parse({"server": {"token": "s3crét"}}, tmp_path)
    page.save_report(cfg, "rep", report_cards)
    page.build_page(cfg, "rep")

    def start(host="127.0.0.1"):
        srv = make_server(cfg, host, 0)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        started.append(srv)
        return f"http://{host}:{srv.server_address[1]}"

    started: list = []
    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def test_token_with_any_characters_and_no_open_redirect(with_token):
    base = with_token()
    good = "k=s3cr%C3%A9t"
    assert raw(base, "GET", "/rep.html?k=%C3%A9")[0] == 403  # a wrong non-ASCII key is refused, not a crash
    assert raw(base, "GET", "/rep.html", {"Cookie": "awbk=\xe9"})[0] == 403
    code, h, _ = raw(base, "GET", "/rep.html?" + good)
    assert code == 302 and h["Location"] == "/rep.html" and "HttpOnly" in h["Set-Cookie"]
    assert raw(base, "GET", "/rep.html", {"Cookie": h["Set-Cookie"].split(";")[0]})[0] == 200
    # "//host/path" would be another site: the redirect always stays on this server
    assert raw(base, "GET", "//evil.example/x?" + good)[1]["Location"] == "/evil.example/x"
    assert raw(base, "GET", "/\\evil.example/x?" + good)[1]["Location"] == "/evil.example/x"


def test_the_bound_address_is_a_valid_host(with_token):
    try:
        base = with_token("127.0.0.2")  # not one of the loopback names: needs the token, answers as itself
    except OSError:
        pytest.skip("this system has no 127.0.0.2")
    cookie = {"Cookie": raw(base, "GET", "/?k=s3cr%C3%A9t")[1]["Set-Cookie"].split(";")[0]}
    assert raw(base, "GET", "/api/status", cookie)[0] == 200
    assert raw(base, "GET", "/api/status", {**cookie, "Host": "other.example"})[0] == 403
