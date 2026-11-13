"""``awb demo``: a complete review loop in one command, on public-domain text.

The sample job: an OCR clean-up agent proposes fixes for a few lines of *Pride and Prejudice* (Jane Austen, 1813,
public domain; Project Gutenberg #1342). Each card shows the "scanned" line as an image and the proposed correction.
"""

from __future__ import annotations

import html
from pathlib import Path

from .config import Config
from .page import build_page, save_report
from .store import post_news

LINES = [
    (
        "It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife.",
        "universaily",
        "universally",
        "0.62",
        "a l / a i confusion at the end of the word",
    ),
    (
        "However little known the feelings or views of such a man may be on his first entering a neighbourhood,",
        "neighbourbood",
        "neighbourhood",
        "0.58",
        "the h of the second syllable looks like b",
    ),
    (
        "“My dear Mr. Bennet,” said his lady to him one day, “have you heard that Netherfield Park is let at last?”",
        "Netherfleld",
        "Netherfield",
        "0.71",
        "a place name: the catalogue has Netherfield",
    ),
    ("Mr. Bennet replied that he had not.", "Bermet", "Bennet", "0.66", "rn / nn"),
    (
        "“Do you not want to know who has taken it?” cried his wife impatiently.",
        "impatient1y",
        "impatiently",
        "0.93",
        "a digit one inside a word",
    ),
    (
        "“You want to tell me, and I have no objection to hearing it.”",
        "ob]ection",
        "objection",
        "0.91",
        "a bracket where a j was printed",
    ),
    (
        "However little known the feelings or views of such a man may be on his first entering a neighbourhood,",
        "neighbourhood",
        "neighborhood",
        "0.31",
        "a spelling change: only if the whole book is modernised",
    ),
]


def line_svg(text: str, n: int) -> str:
    wob = [(-0.4, 0.3), (0.5, -0.2), (-0.2, 0.4), (0.3, 0.1)][n % 4]
    stains = "".join(
        f'<circle cx="{(37 * (i + n) * 7) % 880 + 10}" cy="{(i * 13 + n * 5) % 36 + 6}" r="{1 + i % 3}" fill="#8a7a5a" opacity=".25"/>'
        for i in range(14)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="52" viewBox="0 0 900 52">'
        f'<rect width="900" height="52" fill="#f3ecd8"/>{stains}'
        f'<text x="14" y="33" font-family="Georgia, \'DejaVu Serif\', serif" font-size="17" fill="#2a2418" '
        f'transform="rotate({wob[0]} 14 33) skewX({wob[1]})" textLength="872" lengthAdjust="spacingAndGlyphs">{html.escape(text)}</text></svg>'
    )


def make_demo(cfg: Config, news: bool = True, build: bool = True) -> str:
    """Write the cards (and, by default, the first news item and the page); returns the report name."""
    cfg.ensure_dirs()
    img_dir = cfg.ws / "demo-images"
    img_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    for n, (text, bad, good, conf, why) in enumerate(LINES, 1):
        img = img_dir / f"line{n}.svg"
        img.write_text(line_svg(text, n), encoding="utf-8")
        cards.append(
            {
                "id": f"line-{n}",
                "section": "fixes",
                "title": f"{bad}  →  {good}",
                "subtitle": f"line {n} of 7 · confidence {conf}",
                "why": why,
                "body": f"OCR read:  {bad}\n\nProposed:  {good}",
                "images": [str(img.relative_to(cfg.root)) if img.is_relative_to(cfg.root) else str(img)],
            }
        )
    save_report(
        cfg,
        "ocr-fixes",
        {
            "title": "OCR fixes to confirm",
            "intro": "An agent cleaned the OCR of a public-domain novel and is unsure about these seven lines. "
            "Approve what is right, reject what is wrong, and say why in the box: the agent reads it.",
            "sections": [{"id": "fixes", "title": "Proposed fixes"}],
            "cards": cards,
        },
    )
    if news:
        post_news(
            cfg.news_file,
            "I corrected 212 OCR errors by dictionary and kept these 7 for your eye. "
            "Question: should spelling follow the printed edition (British) or be modernised?",
            "question",
            "Seven lines to confirm",
        )
    if build:
        build_page(cfg, "ocr-fixes")
    return "ocr-fixes"


def write_sample_config(dest: Path) -> Path:
    from importlib import resources

    dest.write_text(
        resources.files("agent_workbench")
        .joinpath("templates", "workbench.toml")
        .read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return dest
