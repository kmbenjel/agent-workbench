#!/usr/bin/env python3
"""Record the review loop for the README: a real server, the real CLI, real clicks in headless Chrome.

    python scripts/make_media.py WORKDIR

Writes into docs/media/: review-loop.gif, card.webp (the landing card), review-page.webp (an Arabic RTL page),
architecture.png (from docs/media/architecture.svg). Needs Chrome/Chromium, ImageMagick (convert) and ffmpeg.
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp_pipe import Chrome  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MEDIA = REPO / "docs" / "media"


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
W, H = 1000, 760
ENV = {**os.environ, "PYTHONPATH": str(REPO / "src")}


def awb(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "agent_workbench", "--root", str(root), *args], capture_output=True, text=True, env=ENV, check=False)


def term_html(title: str, steps: list[tuple[str, str]]) -> str:
    rows = []
    for cmd, outp in steps:
        rows.append(f'<div class="cmd"><span class="p">$</span> {html.escape(cmd)}</div>')
        if outp.strip():
            rows.append(f'<div class="out">{html.escape(outp.rstrip())}</div>')
    return f"""<!doctype html><meta charset=utf-8><style>
body{{margin:0;background:#14161a;color:#d7dae0;font:16px/1.55 "DejaVu Sans Mono",monospace;padding:0}}
.bar{{background:#23262d;padding:9px 16px;color:#9aa0a6;font-size:14px}}
.w{{padding:18px 22px}} .cmd{{margin-top:12px;color:#e6e6e6}} .p{{color:#56d364}} .out{{white-space:pre-wrap;color:#9db4d0;margin:4px 0 0 18px;word-break:break-word}}
.title{{color:#e3b341}}</style><div class=bar>{html.escape(title)}</div><div class=w>{"".join(rows)}</div>"""


def caption(src: Path, text: str, dst: Path) -> None:
    bar = dst.with_suffix(".bar.png")
    subprocess.run(["convert", "-size", f"{W}x58", "xc:#0f1115", "-fill", "#f2f2f2", "-pointsize", "23", "-gravity", "center",
                    "-annotate", "+0+0", text, str(bar)], check=True)
    subprocess.run(["convert", str(src), str(bar), "-append", str(dst)], check=True)
    bar.unlink()


def main(work: Path) -> None:
    work = work.resolve()
    shutil.rmtree(work, ignore_errors=True)
    job = work / "job"
    job.mkdir(parents=True)
    frames = work / "frames"
    frames.mkdir()
    sys.path.insert(0, str(REPO / "src"))
    from agent_workbench.config import parse
    from agent_workbench.demo import make_demo

    cfg = parse({"page": {"image_height": 70}, "server": {"port": PORT}}, job)
    (job / "workbench.toml").write_text(f"[page]\nimage_height = 70\n[server]\nport = {PORT}\n")
    make_demo(cfg, news=False, build=False)
    shutil.copy(cfg.reports_dir / "ocr-fixes.json", job / "cards.json")
    (cfg.reports_dir / "ocr-fixes.json").unlink()

    # 1. the agent builds the page and tells the reviewer ---------------------------------------------------------
    steps = []
    r = awb(job, "page", "build", "ocr-fixes", "--cards", "cards.json", "--title", "OCR fixes to confirm")
    steps.append(('awb page build ocr-fixes --cards cards.json --title "OCR fixes to confirm"', r.stdout.replace(str(job) + "/", "")))
    q = "I corrected 212 OCR errors by dictionary and kept 7 for your eye. Question: should spelling follow the printed edition (British) or be modernised?"
    r = awb(job, "news", "post", q, "--kind", "question", "--title", "Seven lines to confirm")
    steps.append((f'awb news post "{q[:58]}..." --kind question', r.stdout))
    steps.append(("awb serve &    # 127.0.0.1 only", f"review server on http://127.0.0.1:{PORT}/"))
    steps.append(("awb watch      # run as a Claude Code Monitor: one line per remark", ""))
    (work / "t1.html").write_text(term_html("agent session", steps))

    server = subprocess.Popen([sys.executable, "-m", "agent_workbench", "--root", str(job), "serve", "--port", str(PORT)], env=ENV,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    watch_out = work / "watch.txt"
    watcher = subprocess.Popen([sys.executable, "-u", "-m", "agent_workbench", "--root", str(job), "watch", "--settle", "2"], env=ENV,
                               stdout=open(watch_out, "w"), stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    chrome = Chrome(W, H)
    url = f"http://127.0.0.1:{PORT}/ocr-fixes.html"
    try:
        def term(name: str, htmltext: str) -> Path:
            f = work / f"{name}.html"
            f.write_text(htmltext)
            chrome.goto(f"file://{f}", 0.4)
            p = frames / f"{name}.png"
            chrome.shot(str(p))
            return p

        def page(name: str, scroll: str = "0") -> Path:
            chrome.js(f"window.scrollTo(0,{scroll})")
            time.sleep(0.3)
            p = frames / f"{name}.png"
            chrome.shot(str(p))
            return p

        out = []
        out.append(("f1", term("t1", (work / "t1.html").read_text()), "1  The agent builds a review page and asks a question"))
        chrome.goto(url, 1.5)
        out.append(("f2", page("p1"), "2  The reviewer opens it in a browser (127.0.0.1)"))
        for n in (1, 2, 3, 4, 5, 6):
            chrome.click(f'article[data-card="line-{n}"] button.yes')
        time.sleep(1.2)
        out.append(("f3", page("p2", "380"), "3  Ticks: approve what is right"))
        chrome.type('article[data-card="line-7"] textarea', "Keep the British spelling of the printed edition.")
        chrome.click('article[data-card="line-7"] button.no')
        time.sleep(1.2)
        out.append(("f4", page("p3", "document.body.scrollHeight"), "4  Reject with a reason: the comment is saved as typed"))
        chrome.js("window.scrollTo(0,0)")
        chrome.type("#newslist textarea", "British: follow the printed edition.")
        out.append(("f5", page("p4"), "5  ... and answers the agent's question in the news panel"))
        # the landing-page card: the reviewer's view (ticks, a rejection, an answer), a wider window
        chrome.call("Emulation.setDeviceMetricsOverride", width=1200, height=750, deviceScaleFactor=1, mobile=False)
        chrome.js("window.scrollTo(0, 120)")
        time.sleep(0.5)
        chrome.shot(str(frames / "card.png"))
        chrome.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        time.sleep(3.5)  # the watcher settles 2 s after the last change

        # 6. the agent receives the remarks and acts ---------------------------------------------------------------
        got = watch_out.read_text()
        out.append(("f6", term("t2", term_html("agent session: Monitor notifications", [("awb watch", got)])),
                    "6  The agent receives each remark as it settles"))
        steps = []
        r = awb(job, "close", "ocr-fixes", "line-7", "kept British spelling: «Keep the British spelling of the printed edition.»")
        steps.append(('awb close ocr-fixes line-7 "kept British spelling: «Keep the British spelling…»"', r.stdout))
        r = awb(job, "close", "ocr-fixes", "line-6", "the reviewer said it is fine")
        steps.append(('awb close ocr-fixes line-6 "the reviewer said it is fine"', r.stdout + r.stderr))
        for n in (1, 2, 3, 4, 5, 6):
            awb(job, "close", "ocr-fixes", f"line-{n}", "ticked: applied")
        steps.append(("awb close ocr-fixes line-1..6 \"ticked: applied\"", "closed ocr-fixes/line-1 ... closed ocr-fixes/line-6"))
        r = awb(job, "news", "post", "Applied 6 fixes; spelling stays British everywhere. Nothing left to review.", "--kind", "done")
        steps.append(('awb news post "Applied 6 fixes; spelling stays British..." --kind done', r.stdout))
        out.append(("f7", term("t3", term_html("agent session", steps)), "7  It acts, closes each card, and refuses an invented approval"))
        awb(job, "page", "build", "ocr-fixes")
        chrome.goto(url, 1.5)
        out.append(("f8", page("p5"), "8  The page is rebuilt: dealt-with cards are gone, the agent's news is there"))

        for k, src, cap in out:
            caption(src, cap, frames / f"{k}.cap.png")
    finally:
        chrome.close()
        server.kill()
        watcher.kill()

    # GIF ---------------------------------------------------------------------------------------------------------
    durs = {"f1": 3.2, "f2": 2.6, "f3": 2.6, "f4": 3.0, "f5": 2.6, "f6": 3.0, "f7": 4.2, "f8": 4.0}
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{frames / (k + '.cap.png')}'\nduration {durs[k]}\n" for k, _, _ in out) + f"file '{frames / (out[-1][0] + '.cap.png')}'\n")
    MEDIA.mkdir(parents=True, exist_ok=True)
    pal = work / "pal.png"
    vf = "fps=4,scale=860:-1:flags=lanczos"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", f"{vf},palettegen=max_colors=96", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(pal), "-lavfi",
                    f"{vf}[x];[x][1:v]paletteuse=dither=none", "-loop", "0", str(MEDIA / "review-loop.gif")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(frames / "card.png"), "-vf", "scale=1200:-1", "-quality", "82", str(MEDIA / "card.webp")], check=True)
    print("gif", (MEDIA / "review-loop.gif").stat().st_size // 1024, "KB; card", (MEDIA / "card.webp").stat().st_size // 1024, "KB")
    print("watch lines:\n" + got)


AR_LINES = [
    ("على قدر أهل العزم تأتي العزائم", "العزام", "العزائم", "0.55", "حرف الياء والهمزة سقطا من المسح"),
    ("وتأتي على قدر الكرام المكارم", "المكارم", "المكارم", "0.90", "الكلمة صحيحة، والسؤال عن موضع النقطة"),
    ("الخيل والليل والبيداء تعرفني", "والبيداء", "والبيداء", "0.88", "تأكد من الهمزة"),
    ("والسيف والرمح والقرطاس والقلم", "القرطا س", "القرطاس", "0.70", "مسافة زائدة داخل الكلمة"),
]


def arabic(work: Path) -> None:
    """An Arabic (right-to-left) page, for the Arabic docs: real clicks again."""
    from agent_workbench.config import parse
    from agent_workbench.page import build_page, save_report
    from agent_workbench.store import post_news

    job = work / "ar"
    shutil.rmtree(job, ignore_errors=True)
    labels = {"approve": "موافق", "reject": "ارفض", "comment": "تعليقك (اختياري)", "news": "أخبار من الوكيل", "reply": "جوابك",
              "progress": "روجع {done} من {total}", "all": "الكل", "open": "المفتوح", "approved": "الموافَق عليه",
              "rejected": "المرفوض", "saving": "جارٍ الحفظ…", "saved": "حُفظ", "kind_question": "سؤال", "kind_info": "خبر", "kind_done": "تم"}
    cfg = parse({"page": {"lang": "ar", "dir": "rtl", "image_height": 70, "labels": labels}, "server": {"port": PORT}}, job)
    cfg.ensure_dirs()
    (cfg.ws / "img").mkdir(parents=True)
    cards = []
    for n, (text, bad, good, conf, why) in enumerate(AR_LINES, 1):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="700" height="54" viewBox="0 0 700 54"><rect width="700" height="54" fill="#f3ecd8"/>'
               f'<text x="350" y="36" text-anchor="middle" direction="rtl" font-family="Noto Naskh Arabic, serif" font-size="26" fill="#2a2418">{html.escape(text)}</text></svg>')
        (cfg.ws / "img" / f"{n}.svg").write_text(svg, encoding="utf-8")
        cards.append({"id": f"s{n}", "title": f"{bad}  ←  {good}", "subtitle": f"البيت {n} من {len(AR_LINES)} · الثقة {conf}", "why": why,
                      "body": f"قرأ التعرّف الضوئي: {bad}\n\nالمقترح: {good}", "images": [f".workbench/img/{n}.svg"]})
    save_report(cfg, "ocr-ar", {"title": "إصلاحات التعرّف الضوئي للتأكيد", "intro": "أصلح الوكيل نصّ ديوان شعري من الملك العام وبقيت له هذه الأبيات. وافق على الصواب، وارفض الخطأ، واكتب السبب في الخانة: الوكيل يقرؤها.", "cards": cards})
    post_news(cfg.news_file, "صحّحت ٢١٢ خطأً بالمعجم وتركت لك أربعة أبيات. سؤال: هل أُبقي رسم الهمزات كما في الطبعة؟", "question", "أربعة أبيات للتأكيد")
    build_page(cfg, "ocr-ar")
    server = subprocess.Popen([sys.executable, "-m", "agent_workbench", "--root", str(job), "serve", "--port", str(PORT)], env=ENV,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    chrome = Chrome(W, H)
    try:
        chrome.goto(f"http://127.0.0.1:{PORT}/ocr-ar.html", 1.5)
        for n in (1, 2, 3):
            chrome.click(f'article[data-card="s{n}"] button.yes')
        chrome.type('article[data-card="s4"] textarea', "أبقِ المسافة؛ هكذا في الطبعة.")
        chrome.click('article[data-card="s4"] button.no')
        chrome.type("#newslist textarea", "نعم، أبقِ الرسم كما في الطبعة.")
        time.sleep(1)
        chrome.js("window.scrollTo(0, 0)")
        time.sleep(0.4)
        chrome.shot(str(work / "ar.png"))
    finally:
        chrome.close()
        server.kill()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(work / "ar.png"), "-vf", "scale=900:-1", "-quality", "80", str(MEDIA / "review-page-ar.webp")], check=True)


if __name__ == "__main__":
    _work = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/awb-media")
    main(_work)
    arabic(_work)
