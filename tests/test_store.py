import json

import pytest

from agent_workbench import store


def test_make_remark_validates():
    r = store.make_remark("rep", "c1", "approve", "ok", "ab")
    assert r["v"] == 1 and r["verdict"] == "approve" and r["time"].endswith("Z")
    with pytest.raises(ValueError):
        store.make_remark("rep", "c1", "maybe")
    with pytest.raises(ValueError):
        store.make_remark("", "c1")
    assert len(store.make_remark("r", "c", "", "x" * 9000)["comment"]) == 4000


def test_read_jsonl_skips_torn_and_missing(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_text('{"a":1}\nnot json\n[1,2]\n{"b":2}\n{"torn":')
    assert store.read_jsonl(p) == [{"a": 1}, {"b": 2}]
    assert store.read_jsonl(tmp_path / "nope") == []
    assert store.read_jsonl(None) == []


def test_latest_remark_wins_and_later_file_breaks_ties(tmp_path):
    base, live = tmp_path / "base.jsonl", tmp_path / "live.jsonl"
    store.append_jsonl(base, {"time": "2026-01-01T00:00:00Z", "report": "r", "card": "a", "comment": "old"})
    store.append_jsonl(base, {"time": "2026-01-01T00:00:05Z", "report": "r", "card": "b", "comment": "base"})
    store.append_jsonl(live, {"time": "2026-01-01T00:00:05Z", "report": "r", "card": "b", "comment": "live"})
    store.append_jsonl(live, {"time": "2026-01-01T00:00:09Z", "report": "r", "card": "a", "comment": "new"})
    rows = store.latest_remarks([base, live, None], "r")
    assert rows[("r", "a")]["comment"] == "new"
    assert rows[("r", "b")]["comment"] == "live"
    assert store.latest_remarks([base], "other") == {}


def test_news_roundtrip_and_validation(tmp_path):
    p = tmp_path / "news.jsonl"
    n = store.post_news(p, " hello ", "question", "T")
    assert n["id"].startswith("n") and n["text"] == "hello"
    assert store.read_news(p) == [n]
    with pytest.raises(ValueError):
        store.post_news(p, "x", "shout")
    with pytest.raises(ValueError):
        store.post_news(p, "   ")


def test_close_note_must_quote_what_is_on_record(tmp_path):
    rem, closed = tmp_path / "r.jsonl", tmp_path / "c.jsonl"
    store.append_jsonl(rem, store.make_remark("rep", "c1", "", "please   use the older\nscan"))
    store.close_card(closed, [rem], "rep", "c1", "done: «use the older scan»")  # whitespace-insensitive
    with pytest.raises(store.CloseError, match="no such words"):
        store.close_card(closed, [rem], "rep", "c1", "he said «looks fine»")
    with pytest.raises(store.CloseError, match="must quote"):
        store.close_card(closed, [rem], "rep", "c1", "fixed")
    store.close_card(closed, [rem], "rep", "c2", "my judgment: duplicate")
    store.close_card(closed, [rem], "rep", "c3", "anything goes", strict=False)
    assert set(store.latest_closed(closed)) == {("rep", "c1"), ("rep", "c2"), ("rep", "c3")}


def test_reopen_cancels_close(tmp_path):
    closed = tmp_path / "c.jsonl"
    store.close_card(closed, [], "r", "a", "my judgment: x")
    assert ("r", "a") in store.latest_closed(closed)
    store.close_card(closed, [], "r", "a", "REOPEN: wrong")
    assert store.latest_closed(closed) == {}


def test_card_state_ticks_hold_until_the_card_changes():
    card = {"id": "a", "title": "T", "body": "b"}
    ok = {"verdict": "approve", "rev": store.card_rev(card)}
    assert store.card_state(card, None, None) == "open"
    assert store.card_state(card, ok, None) == "closed"
    assert store.card_state(card, ok, None, tick_closes=False) == "open"
    assert store.card_state(card, {"verdict": "reject", "rev": ok["rev"]}, None) == "open"
    redone = {**card, "body": "changed"}
    assert store.card_state(redone, ok, None) == "changed"
    assert store.card_state(card, None, {"rev": ok["rev"]}) == "closed"
    assert store.card_state(redone, None, {"rev": ok["rev"]}) == "changed"
    assert store.card_state(redone, None, {"rev": ""}) == "closed"  # a close with no revision holds


def test_card_rev_prefers_explicit_and_ignores_key_order():
    assert store.card_rev({"id": "a", "rev": "R1", "title": "x"}) == "R1"
    assert store.card_rev({"title": "t", "body": "b"}) == store.card_rev({"body": "b", "title": "t"})
    assert store.card_rev({"title": "t"}) != store.card_rev({"title": "u"})


def test_jsonl_lines_are_single_line(tmp_path):
    p = tmp_path / "x.jsonl"
    store.append_jsonl(p, {"comment": "a\nb"})
    assert len(p.read_text().splitlines()) == 1 and json.loads(p.read_text())["comment"] == "a\nb"
