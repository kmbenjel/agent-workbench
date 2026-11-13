"""The JSONL protocol between the agent and the human reviewer.

Three append-only files in the workspace, one JSON object per line, UTF-8, every record carries ``"v": 1``:

``remarks.jsonl``  what the reviewer did on a card (written by the review server)
    {"v":1,"time":"2026-11-13T10:00:00Z","report":"ocr","card":"c3","verdict":"approve|reject|","comment":"...","rev":"ab12"}
``news.jsonl``     what the agent tells the reviewer (written by ``awb news post``)
    {"v":1,"id":"n1a2b3c","time":"...","kind":"info|question|done","text":"...","title":"","card":""}
``closed.jsonl``   cards the agent has dealt with (written by ``awb close``)
    {"v":1,"time":"...","report":"ocr","card":"c3","note":"...","rev":"ab12","reopen":false}

The latest remark per (report, card) wins; the latest close record per card wins (``reopen`` cancels it). A reply
to a news item is a remark on report ``_news`` whose card is the news id.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
NEWS_REPORT = "_news"
VERDICTS = ("approve", "reject", "")
NEWS_KINDS = ("info", "question", "done")
_lock = threading.Lock()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(line)


def read_jsonl(path: Path | str | None) -> list[dict[str, Any]]:
    """All parseable objects of a JSONL file; a missing file or a torn line is skipped, never an error."""
    if not path or not Path(path).exists():
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


# -- remarks --------------------------------------------------------------------------------------------------
def make_remark(
    report: Any, card: Any, verdict: Any = "", comment: Any = "", rev: Any = ""
) -> dict[str, Any]:
    verdict = str(verdict or "")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS[:2]} or empty, got {verdict!r}")
    report, card = str(report or "")[:60], str(card or "")[:60]
    if not report or not card:
        raise ValueError("a remark needs a report and a card")
    return {
        "v": PROTOCOL_VERSION,
        "time": now_iso(),
        "report": report,
        "card": card,
        "verdict": verdict,
        "comment": str(comment or "")[:4000],
        "rev": str(rev or "")[:40],
    }


def latest_remarks(
    files: Iterable[Path | str | None], report: str | None = None
) -> dict[tuple[str, str], dict[str, Any]]:
    """The latest remark per (report, card). Files are read in order; for equal times a later file wins."""
    rows = [
        d
        for f in files
        for d in read_jsonl(f)
        if d.get("card") and (report is None or d.get("report") == report)
    ]
    rows.sort(key=lambda d: str(d.get("time", "")))  # stable: file order breaks ties
    return {(str(d.get("report")), str(d["card"])): d for d in rows}


def has_content(r: dict[str, Any]) -> bool:
    return bool(r.get("verdict") or r.get("comment"))


# -- news -----------------------------------------------------------------------------------------------------
def post_news(path: Path, text: str, kind: str = "info", title: str = "", card: str = "") -> dict[str, Any]:
    if kind not in NEWS_KINDS:
        raise ValueError(f"kind must be one of {NEWS_KINDS}")
    if not text.strip():
        raise ValueError("news text is empty")
    now = now_iso()
    nid = "n" + hashlib.sha1(f"{now}{text}{time.time_ns()}".encode()).hexdigest()[:6]
    rec = {
        "v": PROTOCOL_VERSION,
        "id": nid,
        "time": now,
        "kind": kind,
        "text": text.strip()[:4000],
        "title": title[:120],
        "card": card[:60],
    }
    append_jsonl(path, rec)
    return rec


def read_news(path: Path | str, limit: int | None = None) -> list[dict[str, Any]]:
    rows = [d for d in read_jsonl(path) if d.get("id")]
    return rows[-limit:] if limit else rows


# -- closing --------------------------------------------------------------------------------------------------
_QUOTE = re.compile(r"[«“\"]([^»”\"]{2,})[»”\"]")


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


class CloseError(ValueError):
    pass


def check_close_note(note: str, comments: list[str]) -> None:
    """The strict rule: a close note quotes the reviewer (and the quote must really be on record), says
    "my judgment" (the agent decided) or starts with REOPEN. It stops an agent from inventing an approval."""
    n = note.strip()
    if n.upper().startswith("REOPEN") or "my judgment" in n.lower() or n.lower().startswith("ticked"):
        return
    quotes = _QUOTE.findall(n)
    if not quotes:
        raise CloseError('the note must quote the reviewer in «…», say "my judgment", or start with REOPEN')
    on_record = [_norm(c) for c in comments]
    for q in quotes:
        if not any(_norm(q) in c for c in on_record):
            raise CloseError(f"no such words on record for this card: «{q}»")


def close_card(
    cfg_closed: Path,
    remark_files: list[Path | str | None],
    report: str,
    card: str,
    note: str,
    rev: str = "",
    strict: bool = True,
) -> dict[str, Any]:
    if strict:
        comments = [
            str(d.get("comment", ""))
            for f in remark_files
            for d in read_jsonl(f)
            if d.get("report") == report and d.get("card") == card
        ]
        check_close_note(note, comments)
    rec = {
        "v": PROTOCOL_VERSION,
        "time": now_iso(),
        "report": report,
        "card": card,
        "note": note.strip()[:1000],
        "rev": rev,
        "reopen": note.strip().upper().startswith("REOPEN"),
    }
    append_jsonl(cfg_closed, rec)
    return rec


def latest_closed(path: Path | str) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for d in read_jsonl(path):
        if not d.get("card"):
            continue
        key = (str(d.get("report")), str(d["card"]))
        if d.get("reopen"):
            out.pop(key, None)
        else:
            out[key] = d
    return out


# -- card state -----------------------------------------------------------------------------------------------
def card_rev(card: dict[str, Any]) -> str:
    """A fingerprint of what the reviewer sees. A tick holds while it is unchanged (so redoing the job does not
    bring back what was already judged) and a changed card comes back marked as changed."""
    if card.get("rev"):
        return str(card["rev"])[:40]
    basis = json.dumps(
        {k: card.get(k) for k in ("title", "subtitle", "body", "images", "why", "extra")},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(basis.encode()).hexdigest()[:10]


def card_state(
    card: dict[str, Any],
    remark: dict[str, Any] | None,
    closed: dict[str, Any] | None,
    tick_closes: bool = True,
) -> str:
    """'open' | 'closed' | 'changed' (was approved or closed on an older revision of the card)."""
    rev = card_rev(card)
    if closed is not None:
        if not closed.get("rev") or closed.get("rev") == rev:
            return "closed"
        return "changed"
    if tick_closes and remark and remark.get("verdict") == "approve":
        if not remark.get("rev") or remark.get("rev") == rev:
            return "closed"
        return "changed"
    return "open"
