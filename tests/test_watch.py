import json

from agent_workbench import store
from agent_workbench.watch import Watcher, format_line, run


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def add(p, card, verdict="", comment="", report="r"):
    store.append_jsonl(p, store.make_remark(report, card, verdict, comment))


def test_remark_is_reported_once_it_settles(tmp_path):
    f, pos, clk = tmp_path / "r.jsonl", tmp_path / "pos", Clock()
    f.write_text("")
    w = Watcher(f, pos, settle=45, clock=clk)
    add(f, "a", "", "hel")
    assert w.poll() == []
    clk.t += 20
    add(f, "a", "", "hello")  # still typing: the clock restarts
    assert w.poll() == []
    clk.t += 44
    assert w.poll() == []
    clk.t += 2
    out = w.poll()
    assert [r["comment"] for r in out] == ["hello"] and w.poll() == []


def test_identical_resave_is_not_reported_twice(tmp_path):
    f, clk = tmp_path / "r.jsonl", Clock()
    f.write_text("")
    w = Watcher(f, tmp_path / "pos", settle=0, clock=clk)
    add(f, "a", "approve", "ok")
    assert len(w.poll()) == 1
    add(f, "a", "approve", "ok")
    assert w.poll() == []
    add(f, "a", "reject", "ok")
    assert len(w.poll()) == 1


def test_restart_resumes_from_saved_position_without_loss_or_repeat(tmp_path):
    f, pos, clk = tmp_path / "r.jsonl", tmp_path / "pos", Clock()
    f.write_text("")
    w = Watcher(f, pos, settle=0, clock=clk)
    add(f, "a", "approve")
    assert len(w.poll()) == 1
    add(f, "b", "reject", "x")  # written while no watcher runs
    w2 = Watcher(f, pos, settle=0, clock=clk)
    out = w2.poll()
    assert [r["card"] for r in out] == ["b"]


def test_first_start_skips_history_unless_asked(tmp_path):
    f = tmp_path / "r.jsonl"
    add(f, "old", "approve")
    assert Watcher(f, tmp_path / "p1", settle=0).poll() == []
    assert [r["card"] for r in Watcher(f, tmp_path / "p2", settle=0, from_start=True).poll()] == ["old"]


def test_half_written_line_waits_for_its_newline(tmp_path):
    f, clk = tmp_path / "r.jsonl", Clock()
    f.write_text("")
    w = Watcher(f, tmp_path / "pos", settle=0, clock=clk)
    line = json.dumps(store.make_remark("r", "a", "approve", "x"))
    with open(f, "a") as fh:
        fh.write(line[:20])
    assert w.poll() == []
    with open(f, "a") as fh:
        fh.write(line[20:] + "\n")
    assert len(w.poll()) == 1


def test_truncated_file_restarts(tmp_path):
    f, clk = tmp_path / "r.jsonl", Clock()
    add(f, "a", "approve", "long comment here")
    w = Watcher(f, tmp_path / "pos", settle=0, from_start=True, clock=clk)
    w.poll()
    f.write_text("")
    add(f, "z", "reject")
    assert [r["card"] for r in w.poll()] == ["z"]


def test_format_and_run(tmp_path):
    r = store.make_remark("rep", "c1", "reject", "two\nlines")
    assert format_line(r) == "REMARK rep c1 verdict=reject «two lines»"
    assert format_line(store.make_remark("_news", "n1", "", "yes")).startswith("REPLY _news n1")
    f = tmp_path / "r.jsonl"
    f.write_text("")
    w = Watcher(f, tmp_path / "pos", settle=0)
    add(f, "a", "approve")
    lines, ticks = [], iter([False, True])
    run(w, lines.append, interval=0, sleep=lambda s: None, stop=lambda: next(ticks))
    assert lines == ["REMARK r a verdict=approve"]
