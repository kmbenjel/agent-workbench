"""The ``awb`` command line: every part of the kit is usable from a shell, so any agent framework can drive it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import CONFIG_NAME, Config, ConfigError, load

DESCRIPTION = """\
agent-workbench: a human-in-the-loop kit for long AI-agent jobs.

  review loop   page build | serve | watch | remarks | news | close | status
  remote        remote up | sync | down | url      (serve the page from a cloud machine over ssh)
  job hygiene   governor | calm wait | netguard | queue run | run | power
  agents        mcp    (an MCP server: post_news, read_remarks, make_review_page, close_card)

Try it:  awb demo     (a complete review loop on public-domain text, in a few seconds)
"""


def out(obj: Any, as_json: bool, text: str = "") -> None:
    print(json.dumps(obj, ensure_ascii=False) if as_json else text)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="awb", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", action="version", version=f"agent-workbench {__version__}")
    ap.add_argument(
        "--config", help=f"path to {CONFIG_NAME} (default: search upwards from here, or AWB_CONFIG)"
    )
    ap.add_argument("--root", help="directory that relative paths use (default: the config's directory)")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    p = sub.add_parser("init", help=f"write a commented {CONFIG_NAME} here")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("demo", help="a complete review loop on public-domain text")
    p.add_argument("dir", nargs="?", default="awb-demo")
    p.add_argument("--serve", action="store_true", help="serve it at once (default: print the command)")
    p.add_argument("--port", type=int, default=8765)

    pg = sub.add_parser("page", help="build review pages from cards").add_subparsers(
        dest="sub", required=True
    )
    p = pg.add_parser("build", help="build REPORT (from --cards FILE, or from its saved report)")
    p.add_argument("report")
    p.add_argument("--cards", help="JSON / JSONL / TSV of cards (see docs/protocol.md)")
    p.add_argument("--title")
    p.add_argument("--intro")
    pg.add_parser("list", help="list the reports and how many cards are open")

    p = sub.add_parser("serve", help="run the review server (127.0.0.1)")
    p.add_argument("--port", type=int)
    p.add_argument("--host")

    p = sub.add_parser("watch", help="stream settled remarks, one line each (works as a Claude Code Monitor)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--once", action="store_true", help="print what is pending now and exit")
    p.add_argument("--from-start", action="store_true", help="first run: include remarks already on file")
    p.add_argument("--settle", type=float, help="seconds a remark must stay unchanged (default from config)")

    p = sub.add_parser("remarks", help="read the latest remark per card")
    p.add_argument("--report")
    p.add_argument("--all", action="store_true", help="include remarks already closed")
    p.add_argument("--since", default="", help="only remarks after this ISO time")
    p.add_argument("--json", action="store_true")

    nw = sub.add_parser("news", help="post to / read the page's news panel").add_subparsers(
        dest="sub", required=True
    )
    p = nw.add_parser("post", help="tell the reviewer something, or ask a question")
    p.add_argument("text", nargs="+")
    p.add_argument("--kind", default="info", choices=["info", "question", "done"])
    p.add_argument("--title", default="")
    p.add_argument("--card", default="")
    p.add_argument("--json", action="store_true")
    p = nw.add_parser("list", help="news items and the reviewer's replies")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("close", help="record that a card was dealt with")
    p.add_argument("report")
    p.add_argument("card")
    p.add_argument(
        "note", nargs="+", help='must quote the reviewer in «…», say "my judgment", or start with REOPEN'
    )

    p = sub.add_parser("status", help="open cards, unhandled remarks, news")
    p.add_argument("--json", action="store_true")

    rm = sub.add_parser(
        "remote", help="serve the pages from a cloud machine over an ssh tunnel"
    ).add_subparsers(dest="sub", required=True)
    for n, h in (
        ("up", "start the machine, push, serve, open the tunnel and the sync loop; print the URL"),
        ("sync", "one round: pull remarks, push the page, stop the machine when no card is open"),
        ("down", "a last pull, then stop the machine, the tunnel and the loop"),
        ("url", "print the page URL"),
        ("loop", "sync every sync_seconds until the machine stops"),
    ):
        rm.add_parser(n, help=h)

    p = sub.add_parser(
        "governor", help="memory governor: pause or kill the largest heavy process before the machine dies"
    )
    p.add_argument("--once", action="store_true")

    cm = sub.add_parser("calm", help="network calm detection").add_subparsers(dest="sub", required=True)
    p = cm.add_parser("wait", help="return once the network has been calm for MINUTES")
    p.add_argument("minutes", type=float, nargs="?")

    p = sub.add_parser("netguard", help="run CMD, pausing it while the network is busy")
    p.add_argument("argv", nargs=argparse.REMAINDER, help="-- CMD ARGS...")

    qu = sub.add_parser("queue", help="resumable download queue").add_subparsers(dest="sub", required=True)
    p = qu.add_parser("run", help="download -> verify -> deliver the items of QUEUE.tsv")
    p.add_argument("queue")
    p.add_argument("--name", help="state name (default: the file name); same name = resume")

    p = sub.add_parser("run", help="rerun CMD after kills until it ends cleanly (waits for free memory)")
    p.add_argument("--log", required=True)
    p.add_argument("argv", nargs=argparse.REMAINDER, help="-- CMD ARGS...")

    sub.add_parser("power", help="battery guard: pause jobs at a low battery")
    sub.add_parser("mcp", help="run the MCP server on stdio")
    return ap


def _dashdash(argv: list[str]) -> list[str]:
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise SystemExit("awb: give the command after --, e.g.  awb netguard -- aria2c ...")
    return argv


def dispatch(a: argparse.Namespace, cfg: Config) -> int:
    from . import page, review, store

    cmd = a.cmd
    if cmd == "init":
        dest = Path(a.root or ".") / CONFIG_NAME
        if dest.exists() and not a.force:
            print(f"awb: {dest} exists (use --force)", file=sys.stderr)
            return 1
        from .demo import write_sample_config

        write_sample_config(dest)
        print(f"wrote {dest}")
    elif cmd == "demo":
        from .config import parse
        from .demo import make_demo
        from .server import serve

        d = Path(a.dir).resolve()
        d.mkdir(parents=True, exist_ok=True)
        dcfg = parse({"server": {"port": a.port}}, d)
        name = make_demo(dcfg)
        print(
            f"demo ready in {d}\n  page: {dcfg.site_dir / (name + '.html')}\n  remarks go to {dcfg.remarks_file}"
        )
        if a.serve:
            print(
                f"\nopen http://127.0.0.1:{a.port}/{name}.html and tick some cards;\n"
                f"in another terminal:  awb --root {d} watch --settle 3 --from-start\n"
            )
            serve(dcfg)
        else:
            print(
                f"\nserve it:  awb --root {d} serve --port {a.port}\nwatch it:  awb --root {d} watch --settle 3 --from-start"
            )
    elif cmd == "page":
        if a.sub == "list":
            for n in page.list_reports(cfg):
                print(f"{n}\t{page.open_count(cfg, n)} open")
            return 0
        if a.cards:
            data = page.load_cards(Path(a.cards))
        else:
            f = cfg.reports_dir / f"{page.report_name(a.report)}.json"
            if not f.is_file():
                raise FileNotFoundError(f"no saved report {a.report!r}; give --cards FILE")
            data = json.loads(f.read_text(encoding="utf-8"))
        for k in ("title", "intro"):
            if getattr(a, k):
                data[k] = getattr(a, k)
        page.save_report(cfg, a.report, data)
        dst = page.build_page(cfg, a.report)
        print(f"{dst}  ({page.open_count(cfg, a.report)} cards open)")
    elif cmd == "serve":
        from .server import serve

        serve(cfg, a.host, a.port)
    elif cmd == "watch":
        from .watch import Watcher, format_line, run

        w = Watcher(
            cfg.remarks_file,
            cfg.ws / "watch.pos",
            cfg.review.settle_seconds if a.settle is None else a.settle,
            from_start=a.from_start,
        )
        if a.once:
            for r in w.drain():
                print(json.dumps(r, ensure_ascii=False) if a.json else format_line(r))
            return 0
        try:
            run(w, print, a.json, cfg.review.poll_seconds)
        except KeyboardInterrupt:
            pass
    elif cmd == "remarks":
        for r in review.read_remarks(cfg, a.report, unhandled=not a.all, since=a.since):
            from .watch import format_line

            print(json.dumps(r, ensure_ascii=False) if a.json else f"{r.get('time')} {format_line(r)}")
    elif cmd == "news":
        if a.sub == "post":
            n = store.post_news(cfg.news_file, " ".join(a.text), a.kind, a.title, a.card)
            out(n, a.json, f"posted {n['id']}")
        else:
            rep = store.latest_remarks([cfg.server.base_remarks, cfg.remarks_file], store.NEWS_REPORT)
            for n in store.read_news(cfg.news_file):
                r = rep.get((store.NEWS_REPORT, n["id"]))
                if a.json:
                    print(json.dumps({**n, "reply": (r or {}).get("comment", "")}, ensure_ascii=False))
                else:
                    print(
                        f"{n['time']} {n['id']} [{n['kind']}] {n['text']}"
                        + (f"\n    reply: {r['comment']}" if r and r.get("comment") else "")
                    )
    elif cmd == "close":
        try:
            rec = review.close(cfg, a.report, a.card, " ".join(a.note))
        except store.CloseError as e:
            print(f"awb: {e}", file=sys.stderr)
            return 1
        print(f"closed {rec['report']}/{rec['card']}")
    elif cmd == "status":
        s = review.status(cfg)
        out(
            s,
            a.json,
            "\n".join(
                [
                    f"open cards: {s['open']}",
                    *(f"  {n}: {c}" for n, c in s["reports"].items()),
                    f"unhandled remarks: {s['unhandled_remarks']}",
                    f"news items: {s['news']}",
                ]
            ),
        )
    elif cmd == "remote":
        from .remote import RemoteError, make_remote

        try:
            r = make_remote(cfg)
            if a.sub == "up":
                print(r.up())
            elif a.sub == "sync":
                print(r.sync())
            elif a.sub == "down":
                r.down()
            elif a.sub == "url":
                print(r.url())
            else:
                r.loop()
        except RemoteError as e:
            print(f"awb: {e}", file=sys.stderr)
            return 1
    elif cmd == "governor":
        from .governor import run as grun

        return grun(cfg, a.once)
    elif cmd == "calm":
        from .calm import wait_calm

        wait_calm(cfg.netpace, a.minutes if a.minutes is not None else cfg.netpace.calm_minutes)
    elif cmd == "netguard":
        from .calm import netguard

        return netguard(cfg.netpace, _dashdash(a.argv))
    elif cmd == "queue":
        from .queue import DownloadQueue, QueueError, read_queue

        try:
            qf = Path(a.queue)
            st = DownloadQueue(cfg, a.name or qf.stem).run(read_queue(qf))
        except QueueError as e:
            print(f"awb: {e}", file=sys.stderr)
            return 1
        return 1 if st.bad else 0
    elif cmd == "run":
        from .resumable import run_resumable

        return run_resumable(_dashdash(a.argv), Path(a.log), cfg.resumable)
    elif cmd == "power":
        from .power import PowerGuard

        PowerGuard(cfg.power).run()
    elif cmd == "mcp":
        from .mcp_server import serve_stdio

        return serve_stdio(cfg)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    try:
        cfg = load(a.config, a.root) if a.cmd not in ("init", "demo") else None
        return dispatch(a, cfg)  # type: ignore[arg-type]
    except (ConfigError, ValueError, FileNotFoundError) as e:
        print(f"awb: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
