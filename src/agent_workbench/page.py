"""Review pages: cards in, one self-contained HTML file out.

A report is ``<workspace>/reports/<name>.json``::

    {"title": "...", "intro": "...", "sections": [{"id": "a", "title": "..."}],
     "cards": [{"id": "c1", "section": "a", "title": "...", "subtitle": "...", "why": "...", "body": "...",
                "images": ["path/or/url.png"], "rev": "optional fingerprint"}]}

Card ids are stable and chosen by the agent: a card keeps its id (and so its remarks) when the page is rebuilt,
and hidden cards never renumber the others.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import html
import json
import re
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Config
from .store import NEWS_REPORT, card_rev, card_state, latest_closed, latest_remarks, read_news

LABELS = {
    "approve": "Approve",
    "reject": "Reject",
    "comment": "Comment (optional)",
    "news": "News from the agent",
    "reply": "Your answer",
    "progress": "{done} of {total} reviewed",
    "all": "All",
    "open": "Open",
    "approved": "Approved",
    "rejected": "Rejected",
    "saving": "saving…",
    "saved": "saved",
    "failed": "not saved",
    "changed": "changed since your last judgment",
    "closed": "{n} card(s) already dealt with are hidden.",
    "noserver": "Not connected to a review server (awb serve): your ticks and comments stay in this browser only.",
    "local": "kept in this browser",
    "kind_info": "info",
    "kind_question": "question",
    "kind_done": "done",
    "no_cards": "Nothing to review. Thank you.",
}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def report_name(name: str) -> str:
    n = _SAFE.sub("-", name).strip("-.")
    if not n or n.startswith("_"):
        raise ValueError(f"bad report name {name!r}: use letters, digits, '-', '_' (not first), '.'")
    return n


def _template(name: str) -> str:
    return resources.files("agent_workbench").joinpath("templates", name).read_text(encoding="utf-8")


def _json_for_html(obj: Any) -> str:
    """JSON that is safe inside a ``<script>`` data block whatever the strings hold: no ``<``, ``>`` or ``&``
    is left for the HTML parser to act on (``</script>``, ``<!--``), and JSON reads the escapes back unchanged."""
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _csp(script: str | None) -> str:
    """The page's own policy: only the kit's script runs (by its hash), so text that slipped past the escaping
    could still not execute; no plugins, no ``<base>``, no forms. Images, styles and fetches are not limited."""
    src = (
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
        if script
        else "'none'"
    )
    return f"script-src {src}; object-src 'none'; base-uri 'none'; form-action 'none'"


def _fill(template: str, values: dict[str, str]) -> str:
    """One pass over the template: a value is never scanned for placeholders, whatever a card's text holds."""
    return re.sub(r"%%([A-Z_]+)%%", lambda m: values.get(m.group(1), m.group(0)), template)


def load_cards(path: Path) -> dict[str, Any]:
    """Cards from a JSON report, a JSONL file of cards or a TSV (id, title, body, images comma-separated)."""
    text = path.read_text(encoding="utf-8")
    suf = path.suffix.lower()
    if suf == ".jsonl":
        return {"cards": [json.loads(ln) for ln in text.splitlines() if ln.strip()]}
    if suf in (".tsv", ".csv"):
        rd = csv.reader(text.splitlines(), delimiter="\t" if suf == ".tsv" else ",")
        cards = []
        for r in rd:
            if not r or r[0].startswith("#"):
                continue
            r = r + [""] * 4
            cards.append(
                {"id": r[0], "title": r[1], "body": r[2], "images": [x for x in r[3].split(",") if x]}
            )
        return {"cards": cards}
    data = json.loads(text)
    return {"cards": data} if isinstance(data, list) else data


def validate_report(data: dict[str, Any]) -> None:
    cards = data.get("cards")
    if not isinstance(cards, list):
        raise ValueError("a report needs a 'cards' list")
    seen: set[str] = set()
    for i, c in enumerate(cards):
        if not isinstance(c, dict) or not str(c.get("id", "")).strip():
            raise ValueError(f"card #{i + 1} has no id")
        cid = str(c["id"])
        if cid in seen:
            raise ValueError(f"duplicate card id {cid!r}")
        seen.add(cid)


def save_report(cfg: Config, name: str, data: dict[str, Any]) -> Path:
    name = report_name(name)
    validate_report(data)
    for c in data["cards"]:
        c["id"] = str(c["id"])
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.reports_dir / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def list_reports(cfg: Config) -> list[str]:
    return sorted(p.stem for p in cfg.reports_dir.glob("*.json")) if cfg.reports_dir.exists() else []


def _copy_image(cfg: Config, name: str, cid: str, i: int, src: str) -> str:
    if re.match(r"^(https?:|data:)", src):
        return src
    p = Path(src)
    p = p if p.is_absolute() else cfg.root / p
    if not p.is_file():
        raise FileNotFoundError(f"card {cid}: image not found: {src}")
    d = cfg.site_dir / name
    d.mkdir(parents=True, exist_ok=True)
    dst = f"{hashlib.sha1(cid.encode()).hexdigest()[:8]}-{i}{p.suffix.lower() or '.img'}"
    shutil.copyfile(p, d / dst)
    return f"{name}/{dst}"


def _states(cfg: Config, name: str, cards: list[dict[str, Any]]) -> list[str]:
    remarks = latest_remarks([cfg.server.base_remarks, cfg.remarks_file], name)
    closed = latest_closed(cfg.closed_file)
    return [
        card_state(
            c, remarks.get((name, str(c["id"]))), closed.get((name, str(c["id"]))), cfg.review.tick_closes
        )
        for c in cards
    ]


def open_count(cfg: Config, name: str | None = None) -> int:
    """Cards still waiting for the reviewer (open or changed) in one report or in all of them."""
    total = 0
    for n in [name] if name else list_reports(cfg):
        data = json.loads((cfg.reports_dir / f"{n}.json").read_text(encoding="utf-8"))
        total += sum(s != "closed" for s in _states(cfg, n, data["cards"]))
    return total


def build_page(cfg: Config, name: str) -> Path:
    name = report_name(name)
    src = cfg.reports_dir / f"{name}.json"
    if not src.is_file():
        raise FileNotFoundError(f"no report {name!r} in {cfg.reports_dir}")
    data = json.loads(src.read_text(encoding="utf-8"))
    validate_report(data)
    cards = data["cards"]
    labels = {**LABELS, **cfg.page.labels}
    E = html.escape
    shutil.rmtree(cfg.site_dir / name, ignore_errors=True)
    states = _states(cfg, name, cards)
    sections = {str(s["id"]): s.get("title", s["id"]) for s in data.get("sections", [])}
    out: list[str] = []
    last, hidden = object(), 0
    for k, (c, st) in enumerate(zip(cards, states, strict=False), 1):
        if st == "closed":
            hidden += 1
            continue
        sec = c.get("section")
        if sec != last and sec is not None:
            n = sum(
                1
                for cc, ss in zip(cards, states, strict=False)
                if cc.get("section") == sec and ss != "closed"
            )
            out.append(f'<h2>{E(str(sections.get(str(sec), sec)))} <span class="mut">({n})</span></h2>')
        last = sec
        pics = "".join(
            f'<a href="{E(u)}" target="_blank" rel="noopener"><img loading="lazy" src="{E(u)}" alt=""></a>'
            for u in (
                _copy_image(cfg, name, str(c["id"]), i, str(s))
                for i, s in enumerate(c.get("images", [])[:6], 1)
            )
        )
        body = "".join(f"<p>{E(par)}</p>" for par in str(c.get("body", "")).split("\n\n") if par.strip())
        out.append(
            f'<article data-card="{E(str(c["id"]))}" data-rev="{E(card_rev(c))}"{" data-changed=1" if st == "changed" else ""} '
            f'class="{"changed" if st == "changed" else "open"}"><div class="head"><span class="n">{k}</span><div>'
            f'<div class="title">{E(str(c.get("title", c["id"])))}</div>'
            + (f'<div class="mut">{E(str(c["subtitle"]))}</div>' if c.get("subtitle") else "")
            + (f'<div class="why">{E(str(c["why"]))}</div>' if c.get("why") else "")
            + (f' <span class="badge">{E(labels["changed"])}</span>' if st == "changed" else "")
            + f'</div></div><div class="body">{body}</div>'
            + (f'<div class="pics">{pics}</div>' if pics else "")
            + f'<div class="act"><button class="yes" data-v="approve" aria-pressed="false">{E(labels["approve"])}</button>'
            f'<button class="no" data-v="reject" aria-pressed="false">{E(labels["reject"])}</button>'
            f'<textarea rows="2" placeholder="{E(labels["comment"])}"></textarea><span class="st"></span></div></article>'
        )
    if not out:
        out.append(f'<p class="mut">{E(labels["no_cards"])}</p>')
    ui = {k: v for k, v in labels.items() if k not in ("noserver", "closed")}
    js = _template("page.js")
    page = _fill(
        _template("page.html"),
        {
            "LANG": E(cfg.page.lang),
            "DIR": E(cfg.page.dir),
            "CSP": E(_csp(js)),
            "TITLE": E(str(data.get("title") or cfg.page.title)),
            "INTRO": E(str(data.get("intro", ""))),
            "NOSERVER": E(labels["noserver"]),
            "CARDS": "\n".join(out),
            "CLOSED": E(labels["closed"].format(n=hidden)) if hidden else "",
            "NEWS": E(labels["news"]),
            "F_ALL": E(labels["all"]),
            "F_OPEN": E(labels["open"]),
            "F_APPROVED": E(labels["approved"]),
            "F_REJECTED": E(labels["rejected"]),
            "IH": str(int(cfg.page.image_height)),
            "CSS": _template("page.css"),
            "JS": js,
            "CFG": _json_for_html({"report": name, "labels": ui, "news": read_news(cfg.news_file, 6)}),
        },
    )
    cfg.site_dir.mkdir(parents=True, exist_ok=True)
    dst = cfg.site_dir / f"{name}.html"
    dst.write_text(page, encoding="utf-8")
    return dst


def build_index(cfg: Config) -> Path:
    E = html.escape
    rows = "".join(
        f'<li><a href="{E(n)}.html">{E(n)}</a> <span class="mut">({open_count(cfg, n)} open)</span></li>'
        for n in list_reports(cfg)
    )
    dst = cfg.site_dir / "index.html"
    cfg.site_dir.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        f'<!doctype html><html lang="{E(cfg.page.lang)}" dir="{E(cfg.page.dir)}"><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{E(_csp(None))}">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{E(cfg.page.title)}</title>'
        f"<style>{_template('page.css')}</style><body><main><h1>{E(cfg.page.title)}</h1><ul>{rows or '<li class=mut>no reports yet</li>'}</ul></main></body></html>",
        encoding="utf-8",
    )
    return dst


__all__ = [
    "LABELS",
    "NEWS_REPORT",
    "build_index",
    "build_page",
    "list_reports",
    "load_cards",
    "open_count",
    "save_report",
]
