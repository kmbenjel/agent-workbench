"""A dependency-free MCP server (JSON-RPC 2.0 over stdio, newline-delimited) exposing the review loop to an agent.

Tools: ``post_news``, ``read_remarks``, ``make_review_page``, ``close_card``. They work on the workspace of the
configuration found from the current directory (or ``AWB_ROOT`` / ``AWB_CONFIG``), exactly as the CLI does.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import IO, Any

from . import __version__, page, review, store
from .config import Config

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "post_news",
        "description": "Tell the human reviewer something on the review page's news panel, or ask a question "
        "(kind=question: the page shows an answer box; the answer comes back through read_remarks "
        "as a remark on report '_news').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": list(store.NEWS_KINDS), "default": "info"},
                "title": {"type": "string"},
                "card": {"type": "string", "description": "a card the news is about"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "read_remarks",
        "description": "Read what the reviewer did: the latest remark per card (approve / reject / comment). "
        "By default only remarks not yet closed. Act on each, then call close_card.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report": {"type": "string"},
                "unhandled": {"type": "boolean", "default": True},
                "since": {"type": "string", "description": "ISO time; only remarks after it"},
            },
        },
    },
    {
        "name": "make_review_page",
        "description": "Create or update a review page from cards. Cards: {id, title, body?, subtitle?, why?, section?, "
        "images?: [path|url]}. Card ids are stable; cards already approved and unchanged stay hidden.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report": {"type": "string"},
                "title": {"type": "string"},
                "intro": {"type": "string"},
                "sections": {"type": "array", "items": {"type": "object"}},
                "cards": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["report", "cards"],
        },
    },
    {
        "name": "close_card",
        "description": "Record that a card's remark was dealt with. The note must quote the reviewer in «…» "
        "(the words must really be on record), say 'my judgment', or start with REOPEN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report": {"type": "string"},
                "card": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["report", "card", "note"],
        },
    },
]


def call_tool(cfg: Config, name: str, args: dict[str, Any]) -> Any:
    if name == "post_news":
        return store.post_news(
            cfg.news_file,
            str(args["text"]),
            args.get("kind", "info"),
            str(args.get("title", "")),
            str(args.get("card", "")),
        )
    if name == "read_remarks":
        return review.read_remarks(
            cfg, args.get("report"), bool(args.get("unhandled", True)), str(args.get("since", ""))
        )
    if name == "make_review_page":
        data = {k: args[k] for k in ("title", "intro", "sections", "cards") if k in args}
        page.save_report(cfg, args["report"], data)
        dst = page.build_page(cfg, args["report"])
        return {
            "page": str(dst),
            "open_cards": page.open_count(cfg, args["report"]),
            "url": f"http://127.0.0.1:{cfg.server.port}/{page.report_name(args['report'])}.html",
        }
    if name == "close_card":
        return review.close(cfg, args["report"], args["card"], args["note"])
    raise KeyError(name)


def handle(cfg: Config, msg: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, the response out (None for notifications)."""
    mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    if mid is None:  # a notification (notifications/initialized ...): no answer
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return ok(
            {
                "protocolVersion": want if want in SUPPORTED else SUPPORTED[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-workbench", "version": __version__},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return err(-32602, f"unknown tool {name!r}")
        try:
            res = call_tool(cfg, name, args)
            return ok(
                {
                    "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=1)}],
                    "isError": False,
                }
            )
        except (ValueError, KeyError, FileNotFoundError, OSError) as e:
            return ok({"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    return err(-32601, f"method not found: {method}")


def serve_stdio(
    cfg: Config,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    on_msg: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("not an object")
        except ValueError:
            stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                )
                + "\n"
            )
            stdout.flush()
            continue
        if on_msg:
            on_msg(msg)
        resp = handle(cfg, msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
