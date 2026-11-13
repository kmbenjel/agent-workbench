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
    t = threading.Thread(target=srv.serve_forever, daemon=True)
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
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert call(base + "/rep.html")[0] == 403
        assert call(base + "/rep.html?k=wrong")[0] == 403
        assert call(base + "/rep.html", headers={"Cookie": "awbk=s3cret"})[0] == 200

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(base + "/rep.html?k=s3cret")
        assert e.value.code == 302 and "awbk=s3cret" in e.value.headers["Set-Cookie"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_base_remarks_are_merged(tmp_path, report_cards):
    base_f = tmp_path / "base.jsonl"
    store.append_jsonl(base_f, store.make_remark("rep", "c1", "reject", "from laptop"))
    cfg = parse({"server": {"base_remarks": str(base_f)}}, tmp_path)
    page.save_report(cfg, "rep", report_cards)
    srv = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _, body = call(f"http://127.0.0.1:{srv.server_address[1]}/api/remarks?report=rep")
        assert json.loads(body)["c1"]["comment"] == "from laptop"
    finally:
        srv.shutdown()
        srv.server_close()
