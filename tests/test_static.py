"""Static review: the agent's side against a fake backend, then against the real Worker code run by Node."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest
from conftest import start_backend, stop_backend

from agent_workbench import cli, page, store
from agent_workbench.config import ConfigError, parse
from agent_workbench.static import StaticError, StaticReview

API = "https://comments.example"


def make_cfg(tmp_path, report_cards, **static):
    cfg = parse({"static": {"api": API, "agent_token": "at", "review_key": "r k", **static}}, tmp_path)
    page.save_report(cfg, "rep", report_cards)
    return cfg


class Backend:
    """What the comment backend holds, behind the Fetch signature."""

    def __init__(self):
        self.remarks: dict[str, dict] = {}
        self.news: list = []
        self.calls: list[tuple[str, str]] = []
        self.status = 200

    def __call__(self, method, url, headers, body, timeout):
        path = url.removeprefix(API)
        self.calls.append((method, path))
        if headers.get("Authorization") != "Bearer at":
            return 403, b'{"error":"wrong key"}'
        if self.status != 200:
            return self.status, b"<html>bad gateway</html>"
        if method == "GET" and path.startswith("/api/remarks?report="):
            return 200, json.dumps(self.remarks.get(path.split("=", 1)[1], {})).encode()
        if method == "PUT" and path == "/api/news":
            assert headers["Content-Type"] == "application/json"
            self.news = json.loads(body)
            return 200, b'{"ok":true}'
        return 404, b'{"error":"not found"}'

    def say(self, report, card, verdict="", comment="", time="2026-11-20T10:00:00Z", **extra):
        self.remarks.setdefault(report, {})[card] = {
            "v": 1,
            "time": time,
            "report": report,
            "card": card,
            "verdict": verdict,
            "comment": comment,
            "rev": "",
            **extra,
        }


def test_pull_appends_what_is_new_and_nothing_twice(tmp_path, report_cards):
    cfg, be = make_cfg(tmp_path, report_cards), Backend()
    s = StaticReview(cfg, be)
    be.say("rep", "c1", "reject", "wrong édition")
    assert [(r["card"], r["verdict"]) for r in s.pull()] == [("c1", "reject")]
    assert s.pull() == []  # the same remark is still on the backend: nothing new
    rows = store.read_jsonl(cfg.remarks_file)
    assert (
        len(rows) == 1 and rows[0]["time"] == "2026-11-20T10:00:00Z" and rows[0]["comment"] == "wrong édition"
    )
    be.say("rep", "c1", "approve", "", time="2026-11-20T10:05:00Z")  # the reviewer changed their mind
    be.say("_news", "n1", "", "British", time="2026-11-20T10:06:00Z")  # and answered a question
    assert [(r["report"], r["card"]) for r in s.pull()] == [("rep", "c1"), ("_news", "n1")]
    assert ("GET", "/api/remarks?report=rep") in be.calls and ("GET", "/api/remarks?report=_news") in be.calls
    # the pulled remarks are ordinary remarks: the agent reads and closes them as ever
    assert cli.main(["--root", str(tmp_path), "close", "_news", "n1", "kept: «British»"]) == 0


def test_pull_never_trusts_the_shape_of_what_comes_back(tmp_path, report_cards):
    cfg, be = make_cfg(tmp_path, report_cards), Backend()
    be.say("rep", "c1", "maybe")  # not a verdict
    be.say("rep", "c2", "approve", "x" * 9000, time="yesterday", extra="field")
    be.remarks["rep"]["c2"]["report"] = "other"
    be.remarks["rep"]["c3"] = "not an object"
    new = StaticReview(cfg, be).pull()
    assert [r["card"] for r in new] == ["c2"]
    r = new[0]
    assert r["report"] == "rep" and len(r["comment"]) == 4000 and "extra" not in r
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["time"])  # a time that is not one becomes now
    be.remarks["rep"] = ["a list"]
    with pytest.raises(StaticError, match="no remarks object"):
        StaticReview(cfg, be).pull()


def test_a_newer_local_remark_is_not_overwritten(tmp_path, report_cards):
    cfg, be = make_cfg(tmp_path, report_cards), Backend()
    store.append_jsonl(
        cfg.remarks_file,
        {**store.make_remark("rep", "c1", "reject", "local"), "time": "2030-01-01T00:00:00Z"},
    )
    be.say("rep", "c1", "approve", "older, from the backend")
    assert StaticReview(cfg, be).pull() == []


def test_news_is_pushed_when_it_changes(tmp_path, report_cards):
    cfg, be = make_cfg(tmp_path, report_cards), Backend()
    s = StaticReview(cfg, be)
    assert s.push_news() == 0 and be.calls == []  # no news: no request
    store.post_news(cfg.news_file, "seven lines for you", "question")
    assert s.push_news() == 1 and be.news[0]["text"] == "seven lines for you"
    assert s.push_news() == 0  # unchanged
    store.post_news(cfg.news_file, "done", "done")
    assert s.sync() == "pulled 0 new remark(s), pushed 2 news item(s)"
    assert StaticReview(cfg, be).sync() == "pulled 0 new remark(s)"


def test_errors_say_what_to_fix(tmp_path, report_cards):
    with pytest.raises(StaticError, match=r"\[static\] api"):
        StaticReview(parse({}, tmp_path))
    with pytest.raises(StaticError, match="AWB_STATIC_TOKEN"):
        StaticReview(parse({"static": {"api": API}}, tmp_path)).pull()
    cfg, be = make_cfg(tmp_path, report_cards, agent_token="stale"), Backend()
    with pytest.raises(StaticError, match=r"answered 403 .* wrong key \(is the agent token"):
        StaticReview(cfg, be).pull()
    cfg, be = make_cfg(tmp_path, report_cards), Backend()
    be.status = 502
    with pytest.raises(StaticError, match="answered 502 .*bad gateway"):
        StaticReview(cfg, be).pull()
    with pytest.raises(ConfigError, match="http"):
        parse({"static": {"api": "comments.example"}}, tmp_path)
    with pytest.raises(ConfigError, match="use https"):  # the keys travel in a header
        parse({"static": {"api": "http://comments.example"}}, tmp_path)
    with pytest.raises(ConfigError, match="user name"):  # "localhost:80" is a user name here, not the host
        parse({"static": {"api": "http://localhost:80@comments.example"}}, tmp_path)
    assert parse({"static": {"api": "http://[::1]"}}, tmp_path).static.api == "http://[::1]"
    assert (
        parse({"static": {"api": "http://localhost:8787/"}}, tmp_path).static.api == "http://localhost:8787"
    )


def test_the_token_never_follows_a_redirect(tmp_path, report_cards):
    import http.server
    import threading

    seen = []

    class Redirects(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/elsewhere")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirects)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        cfg = parse(
            {"static": {"api": f"http://127.0.0.1:{srv.server_address[1]}", "agent_token": "at"}}, tmp_path
        )
        page.save_report(cfg, "rep", report_cards)
        with pytest.raises(StaticError, match="answered 302"):
            StaticReview(cfg).pull()
        assert seen == [("/api/remarks?report=rep", "Bearer at")]  # asked once, went nowhere else
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_unreachable_backend_is_a_plain_message(tmp_path, report_cards):
    cfg = parse(
        {"static": {"api": "http://127.0.0.1:9", "agent_token": "at", "timeout_seconds": 2}}, tmp_path
    )
    with pytest.raises(StaticError, match="cannot reach the comment backend at http://127.0.0.1:9"):
        StaticReview(cfg).pull()
    assert (
        cli.main(["--root", str(tmp_path), "static", "sync"]) == 1
    )  # no [static] api here: a message, exit 1


def test_the_loop_reports_an_outage_once_and_carries_on(tmp_path, report_cards):
    cfg, be = make_cfg(tmp_path, report_cards), Backend()
    said, rounds = [], iter([False, False, False, False, True])
    be.status = 502

    def sleep(_):
        if (
            len(said) == 1
            and be.status == 502
            and sum(c == ("GET", "/api/remarks?report=rep") for c in be.calls) == 2
        ):
            be.status = 200
            be.say("rep", "c2", "approve")

    StaticReview(cfg, be).loop(said.append, sleep, lambda: next(rounds))
    assert len(said) == 2 and "trying again every 20 s" in said[0] and said[1] == "pulled 1 new remark(s)"


def test_links_and_keys(tmp_path, report_cards, monkeypatch):
    cfg = make_cfg(tmp_path, report_cards, page_url="https://reviews.example.org/job/")
    s = StaticReview(cfg, Backend())
    assert s.url("rep") == "https://reviews.example.org/job/rep.html#k=r%20k"
    assert s.url() == "https://reviews.example.org/job/index.html#k=r%20k"
    with pytest.raises(StaticError, match="page_url"):
        StaticReview(make_cfg(tmp_path, report_cards), Backend()).url()
    monkeypatch.setenv("AWB_REVIEW_KEY", "from-env")
    monkeypatch.setenv("AWB_STATIC_TOKEN", "tok")
    cfg = parse({"static": {"api": API + "/"}}, tmp_path)
    assert (cfg.static.review_key, cfg.static.agent_token, cfg.static.api) == ("from-env", "tok", API)


def test_publish_runs_the_command_on_the_site_directory(tmp_path, report_cards):
    cfg = make_cfg(tmp_path, report_cards, publish_cmd="cp -r {site} published && echo sent")
    page.build_page(cfg, "rep")
    assert StaticReview(cfg, Backend()).publish() == "sent"
    assert (tmp_path / "published" / "rep.html").is_file()
    assert "rep.html" in (tmp_path / "published" / "index.html").read_text()  # the index goes up too
    cfg = make_cfg(tmp_path, report_cards, publish_cmd="echo no such host >&2; exit 7")
    with pytest.raises(StaticError, match=r"failed \(7\): no such host"):
        StaticReview(cfg, Backend()).publish()
    with pytest.raises(StaticError, match="publish_cmd is empty"):
        StaticReview(make_cfg(tmp_path, report_cards), Backend()).publish()


def test_pages_built_for_a_static_host_name_the_backend(tmp_path, report_cards):
    cfg = make_cfg(tmp_path, report_cards)
    html = page.build_page(cfg, "rep").read_text()
    block = json.loads(html.split('id="awb-cfg">')[1].split("</script>")[0])
    assert block["api"] == API and "nokey" in block["labels"] and "offline" in block["labels"]
    assert "r k" not in html and "Bearer at" not in html  # no key is ever written into a page
    index = page.build_index(cfg).read_text()
    assert (
        "a.hash = location.hash" in index and "sha256-" in index
    )  # the index hands the key on, under its policy
    plain = parse({}, tmp_path / "plain")
    page.save_report(plain, "rep", report_cards)
    assert "api" not in json.loads(
        page.build_page(plain, "rep").read_text().split('id="awb-cfg">')[1].split("</script>")[0]
    )
    assert "<script" not in page.build_index(plain).read_text()


def test_report_names_fit_a_remark():
    assert page.report_name("a" * 60) == "a" * 60
    with pytest.raises(ValueError, match="at most 60"):
        page.report_name("a" * 61)


# -- the real Worker code, run by Node with a bucket in memory ------------------------------------------------
@pytest.fixture
def worker(tmp_path):
    p, base = start_backend("https://pages.example, https://b.example/", tmp_path)
    yield base
    stop_backend(p)


def req(base, method, path, key="", body=None, origin=None, ctype="application/json"):
    headers = {"Content-Type": ctype}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if origin:
        headers["Origin"] = origin
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return f.status, f.headers, json.loads(f.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, json.loads(e.read() or b"null")


def test_worker_keys_origins_and_limits(worker):
    ok = {"report": "rep", "card": "c1", "verdict": "approve", "comment": "fine"}
    assert req(worker, "GET", "/")[2]["ok"] is True  # health, no key
    assert req(worker, "GET", "/api/remarks?report=rep")[0] == 401
    assert req(worker, "GET", "/api/remarks?report=rep", "nope")[0] == 403
    assert req(worker, "POST", "/api/remark", "at", ok)[0] == 403  # the agent does not write remarks
    assert req(worker, "PUT", "/api/news", "rk", [])[0] == 403  # the reviewer does not write news
    # a browser page elsewhere: refused, and given no CORS headers to read anything with
    code, h, _ = req(worker, "POST", "/api/remark", "rk", ok, origin="https://evil.example")
    assert code == 403 and "Access-Control-Allow-Origin" not in h
    assert req(worker, "GET", "/api/remarks?report=rep", "rk", origin="https://evil.example")[0] == 403
    code, h, _ = req(worker, "OPTIONS", "/api/remark", origin="https://b.example")
    assert code == 204 and h["Access-Control-Allow-Origin"] == "https://b.example"
    assert "Authorization" in h["Access-Control-Allow-Headers"]
    assert req(worker, "OPTIONS", "/api/remark", origin="https://evil.example")[0] == 403
    for bad in (
        {**ok, "verdict": "maybe"},
        {**ok, "report": "../news"},
        {**ok, "report": "a/b"},
        {**ok, "card": ""},
        ["a list"],
    ):
        assert req(worker, "POST", "/api/remark", "rk", bad)[0] == 400, bad
    assert req(worker, "POST", "/api/remark", "rk", b"{not json")[0] == 400
    assert req(worker, "POST", "/api/remark", "rk", ok, ctype="text/plain")[0] == 415
    assert req(worker, "POST", "/api/remark", "rk", {**ok, "comment": "x" * 70000})[0] == 413
    assert req(worker, "GET", "/api/remarks?report=..%2Fnews", "rk")[0] == 400
    assert req(worker, "GET", "/api/nothing", "rk")[0] == 404
    assert req(worker, "GET", "/api/remarks?report=rep", "rk")[2] == {}  # nothing above was stored


def test_worker_stores_the_latest_remark_per_card(worker):
    long = {"report": "rep", "card": "é" * 80, "verdict": "", "comment": "y" * 5000, "rev": "r" * 50}
    code, h, out = req(worker, "POST", "/api/remark", "rk", long, origin="https://pages.example")
    assert code == 200 and out["ok"] and h["Access-Control-Allow-Origin"] == "https://pages.example"
    req(
        worker,
        "POST",
        "/api/remark",
        "rk",
        {"report": "rep", "card": "c1", "verdict": "reject", "comment": "no"},
    )
    req(worker, "POST", "/api/remark", "rk", {"report": "rep", "card": "c1", "verdict": "approve"})
    got = req(worker, "GET", "/api/remarks?report=rep", "at")[2]
    assert set(got) == {"é" * 60, "c1"} and got["c1"]["verdict"] == "approve" and got["c1"]["comment"] == ""
    cut = got["é" * 60]
    assert len(cut["comment"]) == 4000 and len(cut["rev"]) == 40 and cut["v"] == 1
    assert req(worker, "GET", "/api/remarks?report=other", "rk")[2] == {}


def test_the_whole_static_loop_against_the_worker(worker, tmp_path, report_cards, monkeypatch, capsys):
    monkeypatch.setenv("AWB_STATIC_TOKEN", "at")
    monkeypatch.setenv("AWB_REVIEW_KEY", "rk")
    (tmp_path / "workbench.toml").write_text(
        f'[static]\napi = "{worker}"\npage_url = "https://pages.example"\npublish_cmd = "cp -r {{site}} out"\n'
    )
    run = lambda *a: cli.main(["--config", str(tmp_path / "workbench.toml"), *a])  # noqa: E731
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps(report_cards))
    assert run("page", "build", "rep", "--cards", str(cards)) == 0
    assert run("news", "post", "British or modern spelling?", "--kind", "question") == 0
    assert run("static", "publish") == 0 and (tmp_path / "out" / "rep.html").is_file()
    capsys.readouterr()
    assert run("static", "url", "rep") == 0
    assert capsys.readouterr().out.strip() == "https://pages.example/rep.html#k=rk"
    assert run("static", "sync") == 0 and "pushed 1 news item(s)" in capsys.readouterr().out
    # the reviewer, in a browser on the static host
    news = req(worker, "GET", "/api/news", "rk", origin="https://pages.example")[2]
    assert news[0]["kind"] == "question"
    post = lambda **d: req(worker, "POST", "/api/remark", "rk", d, origin="https://pages.example")  # noqa: E731
    assert post(report="rep", card="c2", verdict="reject", comment="keep the British spelling")[0] == 200
    assert post(report="_news", card=news[0]["id"], comment="British")[0] == 200
    # the agent
    assert run("static", "sync") == 0 and "pulled 2 new remark(s)" in capsys.readouterr().out
    assert run("watch", "--once", "--from-start") == 0
    out = capsys.readouterr().out
    assert "REMARK rep c2 verdict=reject «keep the British spelling»" in out and "REPLY _news" in out
    assert run("close", "rep", "c2", "kept: «keep the British spelling»") == 0
    assert run("static", "sync") == 0 and "pulled 0 new remark(s)" in capsys.readouterr().out


def test_worker_saves_at_the_same_moment_lose_nothing(worker):
    """A tick on one card while the comment box of another saves: both land in the report's one object."""
    from concurrent.futures import ThreadPoolExecutor

    def save(n):
        return req(
            worker, "POST", "/api/remark", "rk", {"report": "rep", "card": f"c{n}", "verdict": "approve"}
        )[0]

    for _ in range(3):
        with ThreadPoolExecutor(4) as pool:
            assert list(pool.map(save, range(4))) == [200] * 4
    assert set(req(worker, "GET", "/api/remarks?report=rep", "rk")[2]) == {"c0", "c1", "c2", "c3"}
