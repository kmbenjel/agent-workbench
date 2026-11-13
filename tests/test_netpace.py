import sys
import time

from agent_workbench import calm, netpace
from agent_workbench.config import NetpaceConfig

CFG = NetpaceConfig()


def test_parse_ping_handles_formats():
    out = (
        "64 bytes from x: icmp_seq=1 ttl=64 time=3.21 ms\n64 bytes from x: time<1 ms\nrtt min/avg/max = 1/2/3"
    )
    assert netpace.parse_ping(out) == [3.21, 1.0]
    assert netpace.parse_ping("") == []


def test_rate_drops_when_busy_and_grows_when_calm_within_bounds():
    rc = netpace.RateController(CFG, start_mb_s=2.0)
    rc.update([2.0], [20.0])  # sets the baseline
    r0 = rc.rate
    assert rc.update([2.0, 3.0], [20.0]) > r0  # calm: grows
    r1 = rc.rate
    assert rc.update([25.0], [20.0]) < r1  # lan spike
    r2 = rc.rate
    assert rc.update([2.0], [80.0]) < r2  # internet spike over baseline + slack
    for _ in range(100):
        rc.update([50.0], [20.0])
    assert rc.rate == CFG.min_mb_s * 1e6
    for _ in range(200):
        rc.update([2.0], [20.0])
    assert rc.rate == CFG.max_mb_s * 1e6


def test_between_thresholds_the_rate_holds():
    rc = netpace.RateController(CFG)
    rc.update([2.0], [20.0])
    r = rc.rate
    rc.update([8.0], [20.0])  # above low_ms (6), below high_ms (10)
    assert rc.rate == r


def test_pacer_leaky_bucket_sleeps_to_the_rate():
    now, slept = [0.0], []
    p = netpace.Pacer(CFG, clock=lambda: now[0], sleep=slept.append)
    p.ctl.rate = 1e6
    p.wait(250_000)  # inside the 0.25 s burst
    assert slept == []
    p.wait(1_000_000)  # 1.25 MB queued at 1 MB/s -> 1.0 s after the burst allowance
    assert abs(slept[-1] - 1.0) < 1e-6
    now[0] += 10  # long idle: the bucket is empty again
    slept.clear()
    p.wait(100_000)
    assert slept == []


def test_pacer_off_never_sleeps():
    slept = []
    p = netpace.Pacer(NetpaceConfig(enabled=False), sleep=slept.append)
    p.wait(10**9)
    assert slept == [] and p.status() == "pace off"


def test_paced_copy(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 3000)
    waits = []
    p = netpace.Pacer(NetpaceConfig(enabled=False))
    p.wait = waits.append  # type: ignore[method-assign]
    assert netpace.paced_copy(src, tmp_path / "d" / "b.bin", p, chunk=1024) == 3000
    assert (tmp_path / "d" / "b.bin").read_bytes() == src.read_bytes()
    assert waits == [1024, 1024, 952] and not list((tmp_path / "d").glob("*.part"))


def test_calm_detector():
    d = calm.CalmDetector(CFG)
    assert d.is_calm([2.0], [20.0])
    assert not d.is_calm([9.0], [20.0])  # lan over 8
    assert not d.is_calm([2.0], [40.0])  # wan over baseline + 12
    assert not d.is_calm([], [20.0]) and not d.is_calm([2.0], [])


def test_wait_calm_needs_unbroken_calm():
    t = [0.0]
    readings = iter([([2.0], [20.0])] * 3 + [([30.0], [20.0])] + [([2.0], [20.0])] * 40)
    cfg = NetpaceConfig(router="gw", wan="wan")
    seq = []

    def p(host):
        if host == "gw":
            seq.append(next(readings))
            return seq[-1][0]
        return seq[-1][1]

    logs = []
    calm.wait_calm(
        cfg, 1, ping_fn=p, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], log=logs.append
    )
    # 3 calm rounds, a spike at round 4 resets, then 60 s (3 rounds of 20 s) of calm needed after it
    assert t[0] >= 20 * (4 + 3) and len(seq) <= 12 and logs


def test_pause_gate_pauses_at_once_and_resumes_after_calm():
    g = calm.PauseGate(NetpaceConfig(calm_minutes=1))
    assert g.update([2.0], [20.0], 0) is None
    assert g.update([30.0], [20.0], 5) == "pause"
    assert g.update([30.0], [20.0], 10) is None  # still paused, still busy
    assert g.update([2.0], [20.0], 20) is None  # calm starts
    assert g.update([30.0], [20.0], 40) is None  # broken
    assert g.update([2.0], [20.0], 50) is None
    assert g.update([2.0], [20.0], 109) is None
    assert g.update([2.0], [20.0], 111) == "resume"
    assert g.update([2.0], [20.0], 112) is None


def test_no_answer_does_not_pause():
    g = calm.PauseGate(NetpaceConfig())
    assert g.update([], [], 0) is None and g.update([], [20.0], 1) is None


def test_netguard_stops_and_continues_a_real_process(tmp_path):
    marker = tmp_path / "ticks"
    code = f"import time\nfor i in range(40):\n    open({str(marker)!r},'a').write('x'); time.sleep(0.05)\n"
    readings = iter([([2.0], [20.0]), ([2.0], [20.0]), ([40.0], [20.0])] + [([2.0], [20.0])] * 200)
    state = {"busy_seen": None}
    t = [0.0]
    cfg = NetpaceConfig(router="gw", wan="wan", calm_minutes=0.0)
    cur = []

    def p(host):
        if host == "gw":
            cur.append(next(readings))
            return cur[-1][0]
        return cur[-1][1]

    logs = []

    def sleep(s):
        time.sleep(0.1)
        t[0] += 1
        if any("paused" in x for x in logs) and state["busy_seen"] is None:
            state["busy_seen"] = marker.stat().st_size if marker.exists() else 0

    rc = calm.netguard(
        cfg,
        [sys.executable, "-c", code],
        ping_fn=p,
        sleep=sleep,
        clock=lambda: t[0],
        every=0.1,
        log=logs.append,
    )
    assert rc == 0 and any("paused" in x for x in logs) and any("resumed" in x for x in logs)
    assert marker.stat().st_size == 40
