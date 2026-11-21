import json

import pytest

from agent_workbench import page, store


def test_build_page_escapes_and_lists_cards(cfg, report_cards):
    page.save_report(cfg, "rep", report_cards)
    out = page.build_page(cfg, "rep")
    html = out.read_text()
    assert "First &lt;b&gt;" in html and "First <b>" not in html
    assert 'data-card="c1"' in html and 'data-card="c3"' in html
    assert "Section A" in html and "<p>one</p><p>two</p>" in html


def test_title_is_not_injected_as_html(cfg):
    page.save_report(
        cfg, "x", {"title": "<script>alert(1)</script>", "cards": [{"id": "1", "title": "<img onerror=x>"}]}
    )
    html = page.build_page(cfg, "x").read_text()
    assert "<script>alert(1)" not in html and "<img onerror" not in html


def test_ticked_cards_hide_and_changed_ones_return(cfg, report_cards):
    page.save_report(cfg, "rep", report_cards)
    cards = report_cards["cards"]
    store.append_jsonl(
        cfg.remarks_file, store.make_remark("rep", "c1", "approve", "", store.card_rev(cards[0]))
    )
    store.append_jsonl(cfg.remarks_file, store.make_remark("rep", "c2", "reject", "no"))
    html = page.build_page(cfg, "rep").read_text()
    assert 'data-card="c1"' not in html and 'data-card="c2"' in html
    assert "1 card(s) already dealt with" in html
    assert page.open_count(cfg, "rep") == 2
    # redo the job: card c1 changes -> it comes back marked
    cards[0]["body"] = "different"
    page.save_report(cfg, "rep", {"cards": cards})
    html = page.build_page(cfg, "rep").read_text()
    assert 'data-card="c1"' in html and "data-changed=1" in html


def test_closed_cards_do_not_renumber_the_rest(cfg, report_cards):
    page.save_report(cfg, "rep", report_cards)
    store.close_card(cfg.closed_file, [], "rep", "c1", "my judgment: dup", strict=True)
    html = page.build_page(cfg, "rep").read_text()
    assert (
        '<span class="n">2</span>' in html
        and '<span class="n">3</span>' in html
        and '<span class="n">1</span>' not in html
    )


def test_images_are_copied_and_missing_ones_fail_loudly(cfg):
    (cfg.root / "a.png").write_bytes(b"\x89PNG")
    page.save_report(cfg, "im", {"cards": [{"id": "1", "images": ["a.png", "https://example.org/b.png"]}]})
    html = page.build_page(cfg, "im").read_text()
    assert "im/" in html and "https://example.org/b.png" in html
    assert any((cfg.site_dir / "im").iterdir())
    page.save_report(cfg, "bad", {"cards": [{"id": "1", "images": ["nope.png"]}]})
    with pytest.raises(FileNotFoundError, match="nope.png"):
        page.build_page(cfg, "bad")


def test_report_validation(cfg):
    with pytest.raises(ValueError, match="duplicate"):
        page.save_report(cfg, "d", {"cards": [{"id": "1"}, {"id": "1"}]})
    with pytest.raises(ValueError, match="no id"):
        page.save_report(cfg, "d", {"cards": [{"title": "x"}]})
    with pytest.raises(ValueError):
        page.report_name("_news")
    assert page.report_name("My report!") == "My-report"


def test_load_cards_formats(tmp_path):
    (tmp_path / "a.jsonl").write_text('{"id":"1","title":"t"}\n{"id":"2"}\n')
    (tmp_path / "b.tsv").write_text("# c\n1\tTitle\tBody\ta.png,b.png\n")
    (tmp_path / "c.json").write_text(json.dumps([{"id": "9"}]))
    assert len(page.load_cards(tmp_path / "a.jsonl")["cards"]) == 2
    c = page.load_cards(tmp_path / "b.tsv")["cards"][0]
    assert c["title"] == "Title" and c["images"] == ["a.png", "b.png"]
    assert page.load_cards(tmp_path / "c.json")["cards"][0]["id"] == "9"


def test_rtl_and_labels_come_from_config(tmp_path, report_cards):
    from agent_workbench.config import parse

    cfg = parse({"page": {"lang": "ar", "dir": "rtl", "labels": {"approve": "موافق"}}}, tmp_path)
    page.save_report(cfg, "r", report_cards)
    html = page.build_page(cfg, "r").read_text()
    assert 'dir="rtl"' in html and "موافق" in html


# -- hardening ------------------------------------------------------------------------------------------------
def test_a_card_rev_cannot_break_out_of_its_attribute(cfg):
    page.save_report(cfg, "x", {"cards": [{"id": "1", "rev": '"><img src=x onerror=alert(1)>'}]})
    html = page.build_page(cfg, "x").read_text()
    assert "<img src=x" not in html and 'data-rev="&quot;&gt;&lt;img' in html


def test_placeholders_in_card_text_stay_text(cfg):
    page.save_report(
        cfg,
        "x",
        {
            "title": "%%CARDS%%",
            "intro": "%%JS%%",
            "cards": [{"id": "1", "title": "%%CFG%%", "body": "%%CSS%% %%NEWS%%"}],
        },
    )
    html = page.build_page(cfg, "x").read_text()
    for name in ("CARDS", "JS", "CFG", "CSS", "NEWS"):
        # still there as typed (the title shows twice: <title> and <h1>), and expanded nowhere else
        assert html.count(f"%%{name}%%") == (2 if name == "CARDS" else 1), name
    assert html.count("const CFG = JSON.parse") == 1 and html.count('data-card="1"') == 1


def test_news_cannot_end_the_data_block(cfg, report_cards):
    evil = "</script><script>alert(1)</script><!-- <script> &  "
    store.post_news(cfg.news_file, evil, "info", title=evil[:100])
    page.save_report(cfg, "rep", report_cards)
    html = page.build_page(cfg, "rep").read_text()
    block = html.split('<script type="application/json" id="awb-cfg">')[1].split("</script>")[0]
    assert "<" not in block and ">" not in block and "&" not in block and " " not in block
    assert json.loads(block)["news"][0]["text"] == evil.strip()  # and the reader gets the text back whole
    assert html.count("<script") == 2


def test_the_page_policy_allows_only_the_kit_script(cfg, report_cards):
    import base64
    import hashlib
    import re

    page.save_report(cfg, "rep", report_cards)
    html = page.build_page(cfg, "rep").read_text()
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    script = html.rsplit("<script>", 1)[1].rsplit("</script>", 1)[0]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"script-src &#x27;sha256-{digest}&#x27;" in csp and "unsafe-inline" not in csp
    assert "object-src &#x27;none&#x27;" in csp and "base-uri &#x27;none&#x27;" in csp
    index = page.build_index(cfg).read_text()
    assert "script-src &#x27;none&#x27;" in index and "<script" not in index
