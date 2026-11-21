"""The review server: a small stdlib HTTP server for generated pages and the remark API.

    GET  /                      index of the reports
    GET  /<report>.html, files  the generated site (path-confined)
    GET  /api/remarks?report=R  {card: latest remark} for one report
    POST /api/remark            {report, card, verdict, comment, rev}  -> appended to remarks.jsonl
    GET  /api/news              the agent's news items
    GET  /api/status            {open: N, reports: {name: open}}

It listens on 127.0.0.1 only unless a token is configured. Every request must carry a loopback ``Host`` (or the
address it is bound to, or one of ``allowed_hosts``), POST bodies must be JSON (``Content-Type: application/json``,
no form posts) and ≤ 64 KB, and an ``Origin`` header, when present, must name the same host: a web page elsewhere
cannot write remarks. No response may be framed by another site (a framed page could be made to collect a click on
Approve), SVG and XML files are served sandboxed (a picture opened in its own tab cannot run script), and there
are no directory listings.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
import sys
import time
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Config
from .page import build_index, list_reports, open_count
from .store import append_jsonl, latest_remarks, make_remark, read_news

MAX_BODY = 64 * 1024
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}
WILDCARD = {"", "0.0.0.0", "::", "[::]"}
# what a response may do in the browser: never inside another site's frame; a file that could carry script without
# being a page (SVG, XML) is served inert, so an untrusted picture opened in its own tab stays a picture
PAGE_CSP = "frame-ancestors 'none'"
FILE_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox; frame-ancestors 'none'"
)


def same(a: str, b: str) -> bool:
    """Constant-time equality of two secrets (any characters, unlike ``hmac.compare_digest`` on ``str``)."""
    return hmac.compare_digest(a.encode(), b.encode())


def say(*a: Any) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def make_handler(cfg: Config, bound: str = "") -> type[SimpleHTTPRequestHandler]:
    token = cfg.server.token
    # the cookie holds a digest, not the secret: any characters work in a header, and the token is never stored
    # in the browser
    cookie = hashlib.sha256(f"awb-cookie:{token}".encode()).hexdigest()
    site = cfg.site_dir.resolve()
    own = {bound, f"[{bound}]"} - WILDCARD  # the address the server listens on is a valid Host too

    def host_ok(h: str) -> bool:
        name = h.rsplit(":", 1)[0] if not h.endswith("]") and ":" in h else h
        return (
            name in LOOPBACK
            or name in own
            or h in cfg.server.allowed_hosts
            or name in cfg.server.allowed_hosts
        )

    class Handler(SimpleHTTPRequestHandler):
        server_version = "agent-workbench"
        timeout = 60  # a client that opens a connection and says nothing does not hold a thread for ever

        def log_message(self, *a: Any) -> None:  # one line per remark saved instead (below)
            pass

        # -- plumbing ------------------------------------------------------------------------------------
        def end_headers(self) -> None:
            kind = self.guess_type(urllib.parse.urlsplit(self.path).path)
            inert = "xml" in kind and "xhtml" not in kind  # SVG and XML can carry script when opened in a tab
            self.send_header("Content-Security-Policy", FILE_CSP if inert else PAGE_CSP)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def send_json(self, obj: Any, code: int = 200) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def guard(self) -> bool:
            """Host check, then the token (cookie after a first ``?k=``). False = a response was sent."""
            host = self.headers.get("Host", "")
            if not host_ok(host):
                self.send_json(
                    {
                        "error": "bad host",
                        "hint": f"this server answers its own address only; to reach it as {host!r}, "
                        "add that name to [server] allowed_hosts",
                    },
                    403,
                )
                return False
            if not token:
                return True
            u = urllib.parse.urlsplit(self.path)
            k = urllib.parse.parse_qs(u.query).get("k", [""])[0]
            if k and same(k, token):
                self.send_response(302)
                self.send_header(
                    "Set-Cookie", f"awbk={cookie}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict"
                )
                # one leading slash: "//host/x" would send the browser to another site
                self.send_header("Location", "/" + u.path.lstrip("/"))
                self.end_headers()
                return False
            jar = {
                x.split("=", 1)[0].strip(): x.split("=", 1)[-1]
                for x in (self.headers.get("Cookie") or "").split(";")
                if "=" in x
            }
            if same(jar.get("awbk", ""), cookie):
                return True
            self.send_json({"error": "forbidden"}, 403)
            return False

        def list_directory(
            self, path: Any
        ) -> None:  # the site is pages and their pictures, not a file browser
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        def translate_path(self, path: str) -> str:
            p = posixpath.normpath(urllib.parse.unquote(urllib.parse.urlsplit(path).path)).lstrip("/")
            full = (site / p).resolve()
            return str(full) if full == site or site in full.parents else str(site / "__nope__")

        # -- GET -----------------------------------------------------------------------------------------
        def do_GET(self) -> None:
            if not self.guard():
                return
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/api/remarks":
                rep = q.get("report", q.get("r", [""]))[0]
                rows = latest_remarks([cfg.server.base_remarks, cfg.remarks_file], rep)
                return self.send_json({card: r for (_, card), r in rows.items()})
            if u.path == "/api/news":
                return self.send_json(read_news(cfg.news_file, 50))
            if u.path == "/api/status":
                names = list_reports(cfg)
                return self.send_json(
                    {
                        "open": sum(open_count(cfg, n) for n in names),
                        "reports": {n: open_count(cfg, n) for n in names},
                    }
                )
            if u.path in ("/", "/index.html"):
                build_index(cfg)
                self.path = "/index.html"
            if u.path.endswith(".html"):
                say("opened", urllib.parse.unquote(u.path).lstrip("/"))
            return super().do_GET()

        def do_HEAD(self) -> None:
            if self.guard():
                super().do_HEAD()

        # -- POST ----------------------------------------------------------------------------------------
        def do_POST(self) -> None:
            if not self.guard():
                return
            if urllib.parse.urlsplit(self.path).path != "/api/remark":
                return self.send_json({"error": "not found"}, 404)
            origin = self.headers.get("Origin")
            if (origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host")) or (
                self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")
            ):
                return self.send_json({"error": "cross-origin"}, 403)
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send_json({"error": "send application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                return self.send_json({"error": "bad length"}, 400)
            if n < 0:
                return self.send_json({"error": "bad length"}, 400)
            if n > MAX_BODY:
                return self.send_json({"error": "too large"}, 413)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
                rec = make_remark(
                    d.get("report"),
                    d.get("card"),
                    d.get("verdict", ""),
                    d.get("comment", ""),
                    d.get("rev", ""),
                )
            except (ValueError, TypeError, AttributeError) as e:
                return self.send_json({"error": f"bad request: {e}"}, 400)
            append_jsonl(cfg.remarks_file, rec)
            say(f"saved {rec['report']}/{rec['card']}", rec["verdict"] or "-", rec["comment"][:80])
            return self.send_json({"ok": True, "time": rec["time"]})

    return Handler


def make_server(cfg: Config, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    host = host or cfg.server.host
    if host not in LOOPBACK and not cfg.server.token:
        raise SystemExit(
            f"refusing to listen on {host}: set [server] token (or AWB_TOKEN), or keep 127.0.0.1"
        )
    cfg.ensure_dirs()
    return ThreadingHTTPServer((host, cfg.server.port if port is None else port), make_handler(cfg, host))


def serve(cfg: Config, host: str | None = None, port: int | None = None) -> None:
    srv = make_server(cfg, host, port)
    h, p = srv.server_address[:2]
    say(f"review server on http://{h}:{p}/  remarks -> {cfg.remarks_file}  (Ctrl+C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        srv.server_close()


__all__ = ["make_handler", "make_server", "serve"]
