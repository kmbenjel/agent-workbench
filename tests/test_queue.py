import threading
import time
from pathlib import Path

import pytest

from agent_workbench import queue as q
from agent_workbench.config import QueueConfig, parse


def mk(tmp_path, **qc):
    return parse({"queue": {"jobs": 4, "min_free_gb": 0.0, "deliver_to": "out", **qc}}, tmp_path)


def src_file(tmp_path, name, data=b"hello"):
    p = tmp_path / "src" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    return f"file://{p}", len(data)


def test_read_queue_parses_and_validates(tmp_path):
    f = tmp_path / "q.tsv"
    f.write_text("# comment\n\nfile:///a\t5\tdir/a.bin\twhy\nfile:///b\t0\tb.bin\n")
    items = q.read_queue(f)
    assert [(i.target, i.size, i.reason) for i in items] == [("dir/a.bin", 5, "why"), ("b.bin", 0, "")]
    f.write_text("only\ttwo\n")
    with pytest.raises(q.QueueError, match="expected url"):
        q.read_queue(f)
    f.write_text("u\tbig\tt\n")
    with pytest.raises(q.QueueError, match="not a number"):
        q.read_queue(f)


@pytest.mark.parametrize("t", ["/etc/passwd", "../x", "a/../../x", ""])
def test_unsafe_targets_are_refused(t):
    with pytest.raises(q.QueueError, match="unsafe"):
        q.safe_target(t)


def test_queue_downloads_verifies_delivers_and_cleans_stage(tmp_path):
    cfg = mk(tmp_path)
    items = []
    for i in range(6):
        url, n = src_file(tmp_path, f"f{i}", b"x" * (i + 1))
        items.append(q.Item(url, n, f"sub/f{i}.bin"))
    dq = q.DownloadQueue(cfg, "t1", log=lambda m: None)
    st = dq.run(items)
    assert st.ok == 6 and st.bad == 0
    assert (tmp_path / "out/sub/f3.bin").read_bytes() == b"xxxx"
    assert not list(dq.stage.iterdir())
    assert len(dq.done_f.read_text().split()) == 6


def test_rerun_continues_where_it_stopped(tmp_path):
    cfg = mk(tmp_path)
    good, n = src_file(tmp_path, "good", b"abc")
    items = [q.Item(good, n, "good.bin"), q.Item(f"file://{tmp_path}/missing", 0, "bad.bin")]
    st = q.DownloadQueue(cfg, "t2", log=lambda m: None).run(items)
    assert (st.ok, st.bad) == (1, 1)
    assert "bad.bin" in (cfg.ws / "queue/t2.failed").read_text()
    Path(tmp_path / "missing").write_bytes(b"now here")
    calls = []
    dq = q.DownloadQueue(
        cfg, "t2", fetch=lambda u, o: (calls.append(u), q.download_url(u, o, cfg.queue)), log=lambda m: None
    )
    st = dq.run(items)
    assert st.ok == 1 and st.skipped == 1 and len(calls) == 1  # good.bin is not fetched again
    assert (tmp_path / "out/bad.bin").read_bytes() == b"now here"


def test_verification_failures_are_recorded_and_stage_released(tmp_path):
    cfg = mk(tmp_path, verify_magic="%PDF", verify_tail="%%EOF")
    ok, n_ok = src_file(tmp_path, "ok.pdf", b"%PDF-1.4 ... %%EOF\n")
    bad_magic, n1 = src_file(tmp_path, "m.pdf", b"<html>nope %%EOF")
    bad_tail, n2 = src_file(tmp_path, "t.pdf", b"%PDF truncated")
    wrong_size, _ = src_file(tmp_path, "s.pdf", b"%PDF %%EOF")
    dq = q.DownloadQueue(cfg, "t3", log=lambda m: None)
    st = dq.run(
        [
            q.Item(ok, n_ok, "ok.pdf"),
            q.Item(bad_magic, n1, "m.pdf"),
            q.Item(bad_tail, n2, "t.pdf"),
            q.Item(wrong_size, 3, "s.pdf"),
        ]
    )
    reasons = dict(st.failures)
    assert (
        st.ok == 1
        and "does not start" in reasons["m.pdf"]
        and "no '%%EOF'" in reasons["t.pdf"]
        and "size" in reasons["s.pdf"]
    )
    assert dq.staged == 0 and not list(dq.stage.iterdir())


def test_verify_command(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    assert q.verify_file(p, 3, QueueConfig(verify_cmd="true {file}")) is None
    assert "false rejects" in q.verify_file(p, 3, QueueConfig(verify_cmd="false {file}"))
    assert q.verify_file(tmp_path / "nope", 0, QueueConfig()) is not None


def test_staging_cap_limits_bytes_in_flight(tmp_path):
    cfg = mk(tmp_path, stage_gb=0.000003, jobs=6)  # 3 kB
    peak, cur, lock = [0], [0], threading.Lock()

    def fetch(url, out):
        with lock:
            cur[0] += 1000
            peak[0] = max(peak[0], cur[0])
        out.write_bytes(b"x" * 1000)

    def deliver(src, target):
        time.sleep(0.02)
        with lock:
            cur[0] -= 1000

    dq = q.DownloadQueue(cfg, "t4", fetch=fetch, deliver=deliver, log=lambda m: None)
    st = dq.run([q.Item("u", 1000, f"f{i}") for i in range(12)])
    assert st.ok == 12 and peak[0] <= 3000


def test_oversized_item_still_goes_through_an_empty_stage(tmp_path):
    cfg = mk(tmp_path, stage_gb=0.000001)
    url, n = src_file(tmp_path, "big", b"x" * 5000)
    assert q.DownloadQueue(cfg, "t5", log=lambda m: None).run([q.Item(url, n, "big")]).ok == 1


def test_delivery_failure_keeps_the_staged_file_and_retries(tmp_path):
    cfg = mk(tmp_path, deliver_retries=2)
    attempts = []

    def deliver(src, target):
        attempts.append(target)
        raise OSError("remote away")

    url, n = src_file(tmp_path, "a")
    dq = q.DownloadQueue(cfg, "t6", deliver=deliver, log=lambda m: None)
    st = dq.run([q.Item(url, n, "a.bin")])
    assert st.bad == 1 and len(attempts) == 2 and "remote away" in st.failures[0][1]
    assert len(list(dq.stage.iterdir())) == 1 and dq.staged == 0 and "a.bin" not in dq.done


def test_deliver_modes(tmp_path):
    src = tmp_path / "s"
    src.write_bytes(b"data")
    calls = []

    def run(argv, **kw):
        calls.append(argv)

        class R:
            returncode, stderr = 0, b""

        return R()

    q.deliver_file(src, "d/x.bin", QueueConfig(deliver="rclone", deliver_to="remote:lib/"), tmp_path, run)
    assert calls[-1][:3] == ["rclone", "copyto", str(src)] and calls[-1][3] == "remote:lib/d/x.bin"
    q.deliver_file(
        src, "d/x.bin", QueueConfig(deliver="command", deliver_cmd="scp {src} host:{dst}"), tmp_path, run
    )
    assert calls[-1] == ["scp", str(src), "host:d/x.bin"]
    with pytest.raises(q.QueueError, match="deliver_cmd"):
        q.deliver_file(src, "x", QueueConfig(deliver="command"), tmp_path, run)
    with pytest.raises(q.QueueError, match="unknown"):
        q.deliver_file(src, "x", QueueConfig(deliver="ftp"), tmp_path, run)
    q.deliver_file(src, "d/y.bin", QueueConfig(deliver="local", deliver_to="dest"), tmp_path)
    assert (tmp_path / "dest/d/y.bin").read_bytes() == b"data" and not list(
        (tmp_path / "dest/d").glob("*.part")
    )


def test_download_url_aria2c_falls_back_to_curl(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv[0])

        class R:
            returncode = 1 if argv[0] == "aria2c" else 0

        return R()

    monkeypatch.setattr(q.shutil, "which", lambda x: "/bin/" + x)
    q.download_url("http://x/y", tmp_path / "o", QueueConfig(downloader="auto", net_mb_s=3, jobs=3), run)
    assert calls == ["aria2c", "curl"]


def test_aria2c_command_has_caps_and_connections(tmp_path, monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append(argv)

        class R:
            returncode = 0

        return R()

    q.download_url(
        "http://x/y",
        tmp_path / "o",
        QueueConfig(downloader="aria2c", connections=8, net_mb_s=3, jobs=12),
        run,
    )
    a = seen[0]
    assert "-x8" in a and "-s8" in a and "--max-download-limit=250000" in a and "--continue=true" in a


def test_file_lock_serialises_delivery(tmp_path):
    lock = str(tmp_path / "lock")
    inside, overlap = [0], [False]

    def worker():
        with q.FileLock(lock):
            inside[0] += 1
            overlap[0] |= inside[0] > 1
            time.sleep(0.03)
            inside[0] -= 1

    ts = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not overlap[0]
