import json
import os
import select
import subprocess
import sys

import pytest

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


@pytest.mark.skipif(sys.platform == "win32", reason="select() on a pipe needs POSIX")
def test_watch_lines_cross_a_pipe_at_once(tmp_path):
    """A Monitor reads a pipe: each remark must arrive when it settles, not when a buffer fills."""
    ws = tmp_path / ".workbench"
    ws.mkdir()
    add(ws / "remarks.jsonl", "a", "reject", "keep it")
    p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_workbench",
            "--root",
            str(tmp_path),
            "watch",
            "--from-start",
            "--settle",
            "0",
        ],
        stdout=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": ""},
    )
    try:
        ready, _, _ = select.select([p.stdout], [], [], 20)
        assert ready, "no line reached the pipe while the watcher was still running"
        assert p.stdout.readline().decode().strip() == "REMARK r a verdict=reject «keep it»"
    finally:
        p.kill()
        p.wait()


# -- one watcher per workspace --------------------------------------------------------------------------------
posix = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX")


@posix
def test_the_lock_names_its_holder_and_dies_with_it(tmp_path):
    from agent_workbench.watch import WatcherBusy, take_lock

    lock = tmp_path / "ws" / "watch.lock"
    held = take_lock(lock)
    assert lock.read_text() == str(os.getpid())
    with pytest.raises(WatcherBusy, match=str(os.getpid())):
        take_lock(lock)
    held.close()
    take_lock(lock).close()  # free again: a killed watcher never blocks the next one


@posix
def test_a_second_watcher_leaves_and_says_why(tmp_path, capsys):
    from agent_workbench import cli
    from agent_workbench.watch import take_lock

    held = take_lock(tmp_path / ".workbench" / "watch.lock")
    assert cli.main(["--root", str(tmp_path), "watch"]) == 3
    out = capsys.readouterr()
    assert f"pid {os.getpid()}" in out.out and "--shared" in out.out and out.err.strip() == out.out.strip()
    # reading what is pending now needs no lock
    add(tmp_path / ".workbench" / "remarks.jsonl", "a", "approve")
    assert cli.main(["--root", str(tmp_path), "watch", "--once", "--from-start"]) == 0
    assert "REMARK r a verdict=approve" in capsys.readouterr().out
    held.close()


def test_an_idle_watcher_writes_its_position_once_and_makes_no_workspace(tmp_path):
    ws = tmp_path / "ws"
    w = Watcher(ws / "remarks.jsonl", ws / "watch.pos", settle=0)
    assert w.poll() == [] and not ws.exists()  # nothing to watch yet: no directory appears
    ws.mkdir()
    (ws / "remarks.jsonl").write_text("")
    w.poll()
    first = (ws / "watch.pos").stat().st_mtime_ns
    assert (ws / "watch.pos").read_text() == "0"  # saved, so a restart reads from here, not from the end
    w.poll()
    assert (ws / "watch.pos").stat().st_mtime_ns == first  # unchanged position: no write
    add(ws / "remarks.jsonl", "a", "approve")
    assert [r["card"] for r in w.poll()] == ["a"]
    assert int((ws / "watch.pos").read_text()) == (ws / "remarks.jsonl").stat().st_size
