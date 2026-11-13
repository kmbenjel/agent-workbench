import os
import signal

import pytest

from agent_workbench.config import GovernorConfig
from agent_workbench.governor import Governor, LinuxSystem, Proc


class FakeSystem:
    def __init__(self, procs=()):
        self.free, self.psi, self.pen = 8.0, 0.0, 0.0
        self._procs = {p.pid: p for p in procs}
        self.sent, self.pens, self._browsers = [], [], []
        self.idle, self.sound = 0.0, False

    def free_gb(self):
        return self.free

    def pressure(self):
        return self.psi

    def procs(self):
        return list(self._procs.values())

    def signal(self, pid, sig):
        self.sent.append((pid, sig))
        if sig == signal.SIGKILL:
            self._procs.pop(pid, None)
        return pid in self._procs

    def alive(self, pid):
        return pid in self._procs

    def pen_gb(self):
        return self.pen

    def size_pen(self, gb):
        self.pens.append(gb)

    def idle_minutes(self):
        return self.idle

    def sound_playing(self):
        return self.sound

    def browsers(self):
        return self._browsers


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def gov(tmp_path, procs=(), **cfg):
    sysm, clk = FakeSystem(procs), Clock()
    g = Governor(
        GovernorConfig(**cfg),
        sysm,
        tmp_path / "workers",
        tmp_path / "paused",
        clk,
        lambda m: None,
        self_pid=1,
    )
    return g, sysm, clk


big = lambda pid, mb, name="python3", **k: Proc(pid, name, mb, f"{name} job{pid}", **k)  # noqa: E731


def test_nothing_happens_with_plenty_of_memory(tmp_path):
    g, s, _ = gov(tmp_path, [big(10, 900)])
    assert g.step() == 5
    assert s.sent == []


def test_pressure_pauses_the_largest_eligible_process_only(tmp_path):
    g, s, _ = gov(tmp_path, [big(10, 500), big(11, 900), big(12, 300), big(13, 2000, "firefox")])
    s.free = 1.2
    g.step()
    assert s.sent == [(11, signal.SIGSTOP)]  # 900 MB python; 300 MB too small, firefox not a heavy name
    g.step()
    assert s.sent[-1] == (10, signal.SIGSTOP)  # next round: the next largest, not the same one again
    assert (tmp_path / "paused").read_text().split() == ["10", "11"]


def test_low_memory_kills_instead_of_pausing(tmp_path):
    g, s, _ = gov(tmp_path, [big(10, 500), big(11, 900)])
    s.free = 0.5
    g.step()
    assert s.sent[0] == (11, signal.SIGKILL) and (11, signal.SIGKILL) in s.sent


def test_pressure_alone_triggers_pause_and_kill(tmp_path):
    g, s, _ = gov(tmp_path, [big(10, 900)])
    s.psi = 9
    g.step()
    assert s.sent == [(10, signal.SIGSTOP)]
    s.psi = 25
    g.step()
    assert (10, signal.SIGKILL) in s.sent


def test_protected_processes_are_never_touched(tmp_path):
    procs = [big(10, 5000, unit="gnome-shell.service"), big(11, 5000, "node"), big(12, 900)]
    procs[1].cmd = "node /home/u/.vscode/server/x.js"
    procs += [Proc(2, "python3", 9000, "python3 -m agent_workbench governor")]
    g, s, _ = gov(tmp_path, procs, kill_gb=5.0)
    s.free = 1.0
    g.step()
    killed = [p for p, sig in s.sent if sig == signal.SIGKILL]
    assert killed == [12]


def test_own_pid_and_job_patterns(tmp_path):
    g, s, _ = gov(
        tmp_path,
        [Proc(1, "python3", 5000, "python3 governor"), Proc(7, "weird", 800, "weird --render big.dat")],
        job_patterns=[r"weird --render"],
    )
    s.free = 1.0
    g.step()
    assert s.sent == [(7, signal.SIGSTOP)]


def test_headless_browser_counts_as_heavy(tmp_path):
    g, s, _ = gov(
        tmp_path,
        [Proc(5, "chrome", 700, "chrome --headless", headless=True), Proc(6, "chrome", 900, "chrome")],
    )
    s.free = 1.0
    g.step()
    assert s.sent == [(5, signal.SIGSTOP)]


def test_paused_resume_one_at_a_time_after_calm(tmp_path):
    g, s, clk = gov(tmp_path, [big(10, 900), big(11, 800)])
    s.free = 1.0
    g.step()
    g.step()
    assert g.paused == {10, 11}
    s.free = 8.0
    g.step()
    assert g.paused == {10, 11}  # calm only just began
    clk.t += 21
    g.step()
    assert g.paused == {11} and (10, signal.SIGCONT) in s.sent
    clk.t += 5
    g.step()
    assert g.paused == {11}  # 30 s between resumes
    clk.t += 30
    g.step()
    assert g.paused == set() and (tmp_path / "paused").read_text() == ""


def test_pressure_returning_restarts_the_calm_timer(tmp_path):
    g, s, clk = gov(tmp_path, [big(10, 900)])
    s.free = 1.0
    g.step()
    s.free = 8.0
    g.step()
    clk.t += 15
    s.free, s.psi = 8.0, 3.0  # not calm (pressure >= 2)
    g.step()
    clk.t += 10
    s.psi = 0
    g.step()
    assert g.paused == {10}


def test_restart_picks_up_paused_pids_that_still_live(tmp_path):
    (tmp_path / "paused").write_text("10\n99\n")
    g, s, _ = gov(tmp_path, [big(10, 900)])
    assert g.paused == {10}
    g.resume_all()
    assert (10, signal.SIGCONT) in s.sent and g.paused == set()


def test_critical_brake_kills_and_cuts_workers_then_holds_the_climb(tmp_path):
    g, s, clk = gov(tmp_path, [big(10, 900)], max_workers=6)
    (tmp_path / "workers").write_text("6\n")
    s.free = 0.5
    assert g.step() == 2
    assert g.workers() == 4
    g.step()
    assert g.workers() == 2
    g.step()
    assert g.workers() == 1  # never below 1
    s.free = 20.0
    for _ in range(5):  # pen is big enough for 6, but no climb for the hold period
        clk.t += 130
        g.step()
    assert g.workers() == 1
    clk.t += 1300
    g.step()
    clk.t += 130
    g.step()
    assert g.workers() == 2
    clk.t += 130
    g.step()
    assert g.workers() == 3  # one more per grow period, not a jump


def test_workers_follow_the_pen_down_at_once(tmp_path):
    g, s, _ = gov(tmp_path)
    s.free = 3.9  # pen = 3.9 - 2.5 = 1.4 -> clamped to 2 GB -> (2 - 0.5) / 1.5 = 1 worker
    g.step()
    assert g.workers() == 1 and s.pens[-1] == 2.0


def test_pen_is_sized_from_free_memory_plus_what_the_jobs_hold(tmp_path):
    g, s, _ = gov(tmp_path)
    s.free, s.pen = 6.0, 3.0
    g.step()
    assert s.pens == [6.5]
    s.free = 6.1  # within a quarter GB: no resize
    g.step()
    assert s.pens == [6.5]
    s.free, s.pen = 30.0, 0.0
    g.step()
    assert s.pens[-1] == 10.0  # ceiling


def test_idle_browsers_closed_only_when_opted_in_idle_and_silent(tmp_path):
    g, s, clk = gov(tmp_path, close_idle_browsers=True)
    s._browsers = [500]
    s.free, s.idle = 1.9, 30
    g.step()
    clk.t += 31
    s.sound = True
    g.step()
    assert (500, signal.SIGTERM) not in s.sent
    s.sound = False
    g.step()
    assert (500, signal.SIGTERM) in s.sent
    g2, s2, c2 = gov(tmp_path / "x")  # default: off
    s2._browsers, s2.free, s2.idle = [500], 1.9, 30
    g2.step()
    c2.t += 31
    g2.step()
    assert s2.sent == []


def test_interval_is_short_when_memory_is_short(tmp_path):
    g, s, _ = gov(tmp_path)
    s.free = 3.0
    assert g.step() == 2
    s.free = 9.0
    assert g.step() == 5


def test_real_system_reads_this_machine(tmp_path):
    ls = LinuxSystem()
    assert ls.free_gb() > 0 and ls.pressure() >= 0
    me = [p for p in ls.procs() if p.pid == os.getpid()]
    assert me and me[0].rss_mb > 1
    assert ls.alive(os.getpid()) and ls.pen_gb() == 0.0
    ls.size_pen("")  # no slice: nothing happens


def test_signal_to_a_real_process(tmp_path):
    import subprocess

    p = subprocess.Popen(["sleep", "30"])
    try:
        assert LinuxSystem().signal(p.pid, signal.SIGKILL)
        p.wait(5)
        assert not LinuxSystem().signal(p.pid, 0) or p.returncode is not None
    finally:
        p.kill()


@pytest.mark.parametrize("raw,want", [("3", 3), ("99", 6), ("0", 1), ("junk", 6), ("", 6)])
def test_workers_file_is_clamped(tmp_path, raw, want):
    g, _, _ = gov(tmp_path, max_workers=6)
    (tmp_path / "workers").write_text(raw)
    assert g.workers() == want
