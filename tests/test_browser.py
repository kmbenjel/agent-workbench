"""The page in a real browser (headless Chrome over the DevTools pipe): what unit tests cannot see.

Skipped unless ``AWB_BROWSER=1`` and a Chrome is installed: ``AWB_BROWSER=1 pytest tests/test_browser.py``.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_workbench import page, store
from agent_workbench.config import parse
from agent_workbench.server import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

CHROME = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(
    os.environ.get("AWB_BROWSER") != "1" or not CHROME, reason="set AWB_BROWSER=1 (needs Chrome)"
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)
APPROVE = 'article[data-card="c1"] button.yes'


@pytest.fixture(scope="module")
def chrome():
    from cdp_pipe import Chrome

    c = Chrome()
    yield c
    c.close()


@pytest.fixture
def site(tmp_path, report_cards):
    def make(**conf):
        cfg = parse(conf, tmp_path)
        (tmp_path / "pic.png").write_bytes(PNG)
        report_cards["cards"][0]["images"] = ["pic.png"]
        page.save_report(cfg, "rep", report_cards)
        page.build_page(cfg, "rep")
        return cfg

    return make


@pytest.fixture
def serve():
    started = []

    def start(srv):
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        started.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def wait(chrome, expr, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        v = chrome.js(expr)
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(f"never true in the page: {expr}")


def status(chrome):
    return chrome.js("document.querySelector('article[data-card=\"c1\"] .st').textContent")


def test_review_server_tick_reaches_the_file(chrome, site, serve):
    cfg = site()
    base = serve(make_server(cfg, "127.0.0.1", 0))
    chrome.goto(base + "/rep.html")
    # the script ran under the page's own policy (only its hash is allowed)
    assert wait(chrome, "document.querySelector('#count').textContent") == "0 of 3 reviewed"
    assert chrome.js("document.querySelector('#noserver').hidden") is True
    assert chrome.js("document.querySelector('.pics img').naturalWidth") == 1
    chrome.click(APPROVE)
    wait(chrome, "document.querySelector('article[data-card=\"c1\"] .st').textContent.startsWith('saved')")
    rows = store.read_jsonl(cfg.remarks_file)
    assert rows[-1]["card"] == "c1" and rows[-1]["verdict"] == "approve"


def test_injected_markup_cannot_run_script(chrome, site, serve):
    """Suppose an escaping bug let markup through: the page's policy still refuses to run it."""
    cfg = site()
    f = cfg.site_dir / "rep.html"
    f.write_text(
        f.read_text().replace(
            '<div class="body">',
            '<div class="body"><script>window.pwned = 1</script><img src="x" onerror="window.pwned = 2">'
            '<a id="j" href="javascript:window.pwned = 3">x</a>',
            1,
        )
    )
    base = serve(make_server(cfg, "127.0.0.1", 0))
    chrome.goto(base + "/rep.html")
    wait(chrome, "document.querySelector('#count').textContent")  # the kit's own script still runs
    chrome.click("#j")
    assert chrome.js("String(window.pwned)") == "undefined"


def test_a_picture_with_script_is_inert(chrome, site, serve):
    cfg = site()
    (cfg.site_dir / "rep" / "evil.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>'
        "document.documentElement.setAttribute('data-ran', '1');"
        "fetch('/api/remark', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({report: 'rep', card: 'c2', verdict: 'approve'})})"
        "</script></svg>"
    )
    base = serve(make_server(cfg, "127.0.0.1", 0))
    chrome.goto(base + "/rep/evil.svg", wait=1.5)
    assert chrome.js("document.documentElement.getAttribute('data-ran')") is None
    assert store.read_jsonl(cfg.remarks_file) == []


def test_a_refused_save_is_not_reported_as_saved(chrome, site, serve):
    cfg = site(server={"token": "s3cret"})
    base = serve(make_server(cfg, "127.0.0.1", 0))
    chrome.goto(base + "/rep.html?k=s3cret")
    wait(chrome, "document.querySelector('#count').textContent")
    chrome.call("Network.clearBrowserCookies")  # the server now answers 403 with a JSON body
    chrome.click(APPROVE)
    wait(chrome, "document.querySelector('article[data-card=\"c1\"] .st').textContent === 'not saved'")
    assert store.read_jsonl(cfg.remarks_file) == []


def test_a_static_host_keeps_ticks_in_the_browser(chrome, site, serve):
    """Any file host (GitHub Pages, python -m http.server): no API answers, so the page says so and keeps the
    reviewer's work in the browser, across reloads."""
    cfg = site()
    base = serve(
        http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(Quiet, directory=str(cfg.site_dir))
        )
    )
    chrome.goto(base + "/rep.html")
    wait(chrome, "document.querySelector('#noserver').hidden === false")
    chrome.click(APPROVE)
    assert status(chrome) == "kept in this browser"
    chrome.goto(base + "/rep.html")
    wait(chrome, "document.querySelector('article[data-card=\"c1\"]').className === 'approved'")
    assert chrome.js("document.querySelector('#count').textContent") == "1 of 3 reviewed"
    assert status(chrome) == "kept in this browser"


def test_opened_as_a_file(chrome, site):
    cfg = site()
    url = (cfg.site_dir / "rep.html").as_uri()
    chrome.goto(url)
    wait(chrome, "document.querySelector('#noserver').hidden === false")
    assert chrome.js("document.querySelector('.pics img').naturalWidth") == 1
    chrome.click('article[data-card="c2"] button.no')
    chrome.goto(url)
    wait(chrome, "document.querySelector('article[data-card=\"c2\"]').className === 'rejected'")


# -- static review: the page on a file host, the remarks through the Worker (run by Node) ----------------------
@pytest.fixture
def static_setup(tmp_path, report_cards, serve):
    from conftest import start_backend, stop_backend

    from agent_workbench.static import StaticReview

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    pages = serve(
        http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site_dir)))
    )
    p, api = start_backend(pages, tmp_path)
    try:
        cfg = parse(
            {"static": {"api": api, "page_url": pages, "agent_token": "at", "review_key": "rk"}}, tmp_path
        )
        page.save_report(cfg, "rep", report_cards)
        store.post_news(cfg.news_file, "British or modern spelling?", "question")
        page.build_page(cfg, "rep")
        page.build_index(cfg)
        s = StaticReview(cfg)
        s.sync()  # the news reaches the backend
        yield cfg, s
    finally:
        stop_backend(p)


def test_static_host_tick_and_answer_reach_the_agent(chrome, static_setup):
    cfg, s = static_setup
    chrome.goto(s.url())  # the index, with the key in the link
    chrome.click('a[href*="rep.html"]')  # the index hands the key on
    wait(chrome, "location.pathname === '/rep.html' && location.hash === '#k=rk'")
    wait(chrome, "document.querySelector('#count').textContent")
    assert chrome.js("document.querySelector('#noserver').hidden") is True
    chrome.click(APPROVE)
    wait(chrome, "document.querySelector('article[data-card=\"c1\"] .st').textContent.startsWith('saved')")
    wait(chrome, "!!document.querySelector('#newslist textarea')")
    chrome.type("#newslist textarea", "British")
    new = {(r["report"], r["card"]): r for r in s.pull()}
    assert new[("rep", "c1")]["verdict"] == "approve"
    assert [r["comment"] for k, r in new.items() if k[0] == "_news"] == ["British"]
    # the key is remembered for this site: the bare address works on the next visit, and shows what was saved
    chrome.goto(cfg.static.page_url + "/rep.html")
    wait(chrome, "document.querySelector('article[data-card=\"c1\"]').className === 'approved'")
    assert status(chrome).startswith("saved")


def test_static_host_wrong_key_says_so_and_keeps_the_work(chrome, static_setup):
    cfg, s = static_setup
    chrome.goto(cfg.static.page_url + "/rep.html#k=not-the-key")
    wait(chrome, "document.querySelector('#noserver').hidden === false")
    assert "no valid review key" in chrome.js("document.querySelector('#noserver').textContent")
    chrome.click(APPROVE)
    assert status(chrome) == "kept in this browser"
    assert s.pull() == []


def test_the_demo_shows_its_pictures_and_news(chrome, tmp_path, serve):
    """`awb demo`, served and as the static copy in docs/demo: SVG pictures render, the question is on the page."""
    from agent_workbench.demo import make_demo

    cfg = parse({}, tmp_path)
    name = make_demo(cfg)
    for url in (
        serve(make_server(cfg, "127.0.0.1", 0)) + f"/{name}.html",
        (cfg.site_dir / f"{name}.html").as_uri(),
        (Path(__file__).resolve().parents[1] / "docs" / "demo" / "index.html").as_uri(),
    ):
        chrome.goto(url)
        assert wait(chrome, "document.querySelector('#count').textContent") == "0 of 7 reviewed", url
        widths = chrome.js(
            "[...document.querySelectorAll('.pics img')].map(i => (i.scrollIntoView(), i.naturalWidth))"
        )
        wait(
            chrome, "[...document.querySelectorAll('.pics img')].every(i => i.complete && i.naturalWidth > 0)"
        )
        assert len(widths) == 7, url
        assert wait(chrome, "document.querySelector('#newslist').textContent.includes('Question')"), url
