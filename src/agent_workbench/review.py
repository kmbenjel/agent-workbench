"""The agent's side of the review loop: read what the reviewer said, close what was dealt with."""

from __future__ import annotations

from typing import Any

from .config import Config
from .page import list_reports, open_count
from .store import (
    NEWS_REPORT,
    close_card,
    has_content,
    latest_closed,
    latest_remarks,
    read_news,
)


def remark_files(cfg: Config) -> list[Any]:
    return [cfg.server.base_remarks, cfg.remarks_file]


def read_remarks(
    cfg: Config, report: str | None = None, unhandled: bool = True, since: str = ""
) -> list[dict[str, Any]]:
    """Latest remark per card, oldest first. ``unhandled`` keeps those with no close record at or after them
    (an approval with no comment is a remark too: it approves the proposal shown). ``since`` is an ISO time."""
    rows = latest_remarks(remark_files(cfg), report)
    closed = latest_closed(cfg.closed_file)
    out = []
    for (rep, card), r in rows.items():
        if not has_content(r) or (since and str(r.get("time", "")) <= since):
            continue
        c = closed.get((rep, card))
        if unhandled and c and str(c.get("time", "")) >= str(r.get("time", "")):
            continue
        out.append(r)
    return sorted(out, key=lambda r: str(r.get("time", "")))


def close(cfg: Config, report: str, card: str, note: str) -> dict[str, Any]:
    """Record that a card was dealt with. Strict mode (default) makes the note quote the reviewer."""
    rev = ""
    f = cfg.reports_dir / f"{report}.json"
    if f.is_file():
        import json

        from .store import card_rev

        for c in json.loads(f.read_text(encoding="utf-8")).get("cards", []):
            if str(c.get("id")) == card:
                rev = card_rev(c)
    if report == NEWS_REPORT:
        rev = ""
    return close_card(cfg.closed_file, remark_files(cfg), report, card, note, rev, cfg.review.strict_close)


def status(cfg: Config) -> dict[str, Any]:
    names = list_reports(cfg)
    return {
        "reports": {n: open_count(cfg, n) for n in names},
        "open": sum(open_count(cfg, n) for n in names),
        "unhandled_remarks": len(read_remarks(cfg)),
        "news": len(read_news(cfg.news_file)),
    }
