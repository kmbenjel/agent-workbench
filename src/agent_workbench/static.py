"""Static review: the pages live on any static host, the remarks go through a small comment backend.

Nothing of the agent's stays up for the review: the site directory is uploaded once (``publish_cmd``: rsync, a
bucket, GitHub Pages, Cloudflare Pages ...) and the page talks to a backend that speaks four requests::

    GET  /api/remarks?report=R   {card: latest remark}        reviewer key or agent token
    POST /api/remark             {report, card, verdict, comment, rev}       reviewer key
    GET  /api/news               [news items]                 reviewer key or agent token
    PUT  /api/news               [news items]                                agent token

``examples/cloudflare-worker/`` is such a backend (a Cloudflare Worker and one R2 bucket). The agent's side is
here: :meth:`StaticReview.pull` appends what the reviewer did to the local ``remarks.jsonl``, so ``awb watch``,
``awb remarks`` and page builds work exactly as with the local server, and :meth:`push_news` sends the news panel.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .config import Config
from .page import build_index, list_reports
from .store import NEWS_REPORT, append_jsonl, latest_remarks, make_remark, read_news

# (method, url, headers, body, timeout) -> (status, body)
Fetch = Callable[[str, str, dict[str, str], "bytes | None", float], "tuple[int, bytes]"]
_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


class StaticError(RuntimeError):
    """A problem with the static setup or the comment backend, worded for the person who has to fix it."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:  # a redirect would carry the token to another host
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    """One request, never redirected; any status comes back as (status, body)."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise StaticError(f"cannot reach the comment backend at {url.split('/api/')[0]}: {e}") from e


class StaticReview:
    def __init__(self, cfg: Config, fetch: Fetch = http) -> None:
        self.cfg, self.sc, self.fetch = cfg, cfg.static, fetch
        if not self.sc.api:
            raise StaticError('set [static] api = "https://..." (the comment backend) in workbench.toml')

    # -- the backend ---------------------------------------------------------------------------------------
    def _call(self, method: str, path: str, body: Any = None) -> Any:
        if not self.sc.agent_token:
            raise StaticError("set AWB_STATIC_TOKEN (or [static] agent_token): the backend's agent token")
        headers = {"Authorization": f"Bearer {self.sc.agent_token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        code, raw = self.fetch(method, f"{self.sc.api}/api/{path}", headers, data, self.sc.timeout_seconds)
        try:
            out = json.loads(raw or b"null")
        except ValueError:
            out = None
        if not 200 <= code < 300:
            said = out.get("error") if isinstance(out, dict) else raw[:120].decode("utf-8", "replace")
            hint = " (is the agent token the one the backend holds?)" if code in (401, 403) else ""
            raise StaticError(f"the comment backend answered {code} to {method} /api/{path}: {said}{hint}")
        return out

    def pull(self) -> list[dict[str, Any]]:
        """Append to ``remarks.jsonl`` what the reviewer did since the last pull; returns the new remarks."""
        new: list[dict[str, Any]] = []
        files = [self.cfg.server.base_remarks, self.cfg.remarks_file]
        for name in [*list_reports(self.cfg), NEWS_REPORT]:
            got = self._call("GET", "remarks?report=" + urllib.parse.quote(name))
            if not isinstance(got, dict):
                raise StaticError(f"the comment backend sent no remarks object for report {name!r}")
            have = latest_remarks(files, name)
            for card, r in got.items():
                if not isinstance(r, dict):
                    continue
                try:  # the same limits as a remark posted to the local server; the report is the one we asked for
                    rec = make_remark(
                        name, card, r.get("verdict", ""), r.get("comment", ""), r.get("rev", "")
                    )
                except ValueError:
                    continue
                if _ISO.fullmatch(str(r.get("time", ""))):
                    rec["time"] = r["time"]
                old = have.get((name, rec["card"]))
                same = old is not None and all(
                    old.get(k, "") == rec[k] for k in ("verdict", "comment", "rev")
                )
                if same or (old is not None and str(old.get("time", "")) > rec["time"]):
                    continue
                append_jsonl(self.cfg.remarks_file, rec)
                new.append(rec)
        return new

    def push_news(self) -> int:
        """Send the news panel to the backend when it changed; returns how many items went (0 = unchanged)."""
        items = read_news(self.cfg.news_file, 50)
        mark = hashlib.sha1(json.dumps([self.sc.api, items], sort_keys=True).encode()).hexdigest()
        seen = self.cfg.ws / "static.news"
        if not items or (seen.exists() and seen.read_text().strip() == mark):
            return 0
        self._call("PUT", "news", items)
        seen.parent.mkdir(parents=True, exist_ok=True)
        seen.write_text(mark)
        return len(items)

    def sync(self) -> str:
        new, sent = self.pull(), self.push_news()
        return f"pulled {len(new)} new remark(s)" + (f", pushed {sent} news item(s)" if sent else "")

    def loop(
        self,
        say: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        stop: Callable[[], bool] = lambda: False,
    ) -> None:
        """Sync every ``sync_seconds``; a backend that is away for a moment is reported once and tried again."""
        down = False
        while not stop():
            try:
                new, sent = self.pull(), self.push_news()
                if new or sent or down:
                    say(
                        f"pulled {len(new)} new remark(s)" + (f", pushed {sent} news item(s)" if sent else "")
                    )
                down = False
            except StaticError as e:
                if not down:
                    say(f"awb: {e} (trying again every {self.sc.sync_seconds:g} s)")
                down = True
            sleep(self.sc.sync_seconds)

    # -- the pages -----------------------------------------------------------------------------------------
    def publish(self) -> str:
        """Write the index, run ``publish_cmd`` (``{site}`` = the site directory), return what it printed."""
        if not self.sc.publish_cmd:
            raise StaticError(
                '[static] publish_cmd is empty: set the command that uploads the site, e.g. "rsync -a {site}/ host:/var/www/reviews/"'
            )
        build_index(self.cfg)  # the list of reports, as of now, goes up with the pages
        cmd = self.sc.publish_cmd.replace("{site}", shlex.quote(str(self.cfg.site_dir)))
        r = subprocess.run(cmd, shell=True, cwd=self.cfg.root, capture_output=True, text=True)  # noqa: S602
        if r.returncode:
            raise StaticError(f"publish_cmd failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-400:]}")
        return (r.stdout + r.stderr).strip()

    def url(self, report: str | None = None) -> str:
        """The reviewer's link: the published page, with the key after ``#`` (a fragment never reaches the host)."""
        if not self.sc.page_url:
            raise StaticError('set [static] page_url = "https://..." (where the site directory is published)')
        key = f"#k={urllib.parse.quote(self.sc.review_key, safe='')}" if self.sc.review_key else ""
        return f"{self.sc.page_url}/{urllib.parse.quote(report) + '.html' if report else 'index.html'}{key}"


__all__ = ["Fetch", "StaticError", "StaticReview", "http"]
