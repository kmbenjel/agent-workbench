import signal
from pathlib import Path

from agent_workbench.config import PowerConfig, ResumableConfig
from agent_workbench.power import PowerGuard
from agent_workbench.resumable import run_resumable


def test_job_that_succeeds_runs_once(tmp_path):
    log = tmp_path / "l.log"
    assert run_resumable(["x"], log, ResumableConfig(), runner=lambda a: 0) == 0
    assert log.read_text().count("run:") == 1 and log.read_text().rstrip().endswith("done")


def test_killed_job_is_rerun_after_memory_is_back(tmp_path):
    log = tmp_path / "l.log"
    rcs, sleeps = iter([137, 137, 0]), []
    free = iter([1, 5, 5, 5, 5, 1, 5, 5, 5, 5] + [5] * 20)  # needs 4 calm polls in a row each time

    def runner(argv):
        with open(log, "a") as f:
            f.write("progress 1\nprogress 2\nprogress 3\nprogress 4\n")  # the job made progress
        return next(rcs)

    rc = run_resumable(
        ["job"],
        log,
        ResumableConfig(calm_polls=4, poll_seconds=1),
        runner=runner,
        free=lambda: next(free),
        sleep=sleeps.append,
    )
    assert rc == 0 and log.read_text().count("run:") == 3
    assert len(sleeps) == 10  # each wait: one low reading (resets the count), then 4 calm polls


def test_gives_up_after_max_stalls_without_progress(tmp_path):
    log = tmp_path / "l.log"
    rc = run_resumable(
        ["job"],
        log,
        ResumableConfig(max_stalls=3, calm_polls=1),
        runner=lambda a: 7,
        free=lambda: 99,
        sleep=lambda s: None,
    )
    assert rc == 7 and log.read_text().count("run:") == 3 and "stopped: 3 restarts" in log.read_text()


def test_progress_resets_the_stall_counter(tmp_path):
    log = tmp_path / "l.log"
    n = [0]

    def runner(a):
        n[0] += 1
        if n[0] % 2:  # every other run makes progress
            with open(log, "a") as f:
                f.write("a\nb\nc\nd\n")
        return 0 if n[0] == 8 else 1

    assert (
        run_resumable(
            ["j"],
            log,
            ResumableConfig(max_stalls=2, calm_polls=1),
            runner=runner,
            free=lambda: 99,
            sleep=lambda s: None,
        )
        == 0
        and n[0] == 8
    )


def test_real_subprocess_run(tmp_path):
    log = tmp_path / "l.log"
    assert run_resumable(["sh", "-c", "echo hi"], log, ResumableConfig()) == 0
    assert "hi" in log.read_text()


class Guard:
    def __init__(self, tmp_path, ac, pct, **kw):
        (tmp_path / "bat").mkdir(exist_ok=True)
        (tmp_path / "ac").mkdir(exist_ok=True)
        self.bat, self.ac = tmp_path / "bat", tmp_path / "ac"
        self.set(ac, pct)
        self.sent, self.slept, self.clock_t, self.logs = [], [], [0.0], []
        self.g = PowerGuard(
            PowerConfig(job_patterns=["job"], **kw),
            find_pids=lambda p: [11, 12],
            send=lambda pid, s: self.sent.append((pid, s)),
            run_sleep=lambda c: self.slept.append(c),
            clock=lambda: self.clock_t[0],
            sleep=self.nap,
            log=self.logs.append,
            battery=self.bat,
            ac=self.ac,
        )

    def nap(self, s):
        self.clock_t[0] += s

    def set(self, ac, pct):
        (self.ac / "online").write_text(str(int(ac)))
        (self.bat / "capacity").write_text(str(pct))


def test_pauses_on_second_low_reading_and_resumes_on_charger(tmp_path):
    h = Guard(tmp_path, False, 5)
    h.g.tick()
    assert h.sent == []  # one reading is not enough
    h.g.tick()
    assert h.sent == [(11, signal.SIGSTOP), (12, signal.SIGSTOP)] and h.g.paused
    h.g.tick()
    assert len(h.sent) == 2  # already paused
    h.set(True, 5)
    h.g.tick()
    assert h.sent[-2:] == [(11, signal.SIGCONT), (12, signal.SIGCONT)] and not h.g.paused


def test_plugging_in_during_the_warning_cancels(tmp_path):
    h = Guard(tmp_path, False, 5)
    h.g.tick()
    orig = h.g.sleep

    def plug(s):
        h.set(True, 5)
        orig(s)

    h.g.sleep = plug
    h.g.tick()
    assert h.sent == [] and not h.g.paused


def test_healthy_battery_never_pauses_and_polls_faster_when_near(tmp_path):
    h = Guard(tmp_path, False, 80)
    assert h.g.tick() == 300 and h.sent == []
    h.set(False, 12)
    assert h.g.tick() == 60


def test_sleep_must_really_jump_the_clock(tmp_path):
    h = Guard(tmp_path, False, 5, sleep_cmd="systemctl hibernate", held_seconds=300)
    h.g.tick()
    h.g.tick()
    assert h.slept == ["systemctl hibernate"]
    assert any("did not sleep" in m for m in h.logs)  # the fake sleep returned at once: no wall-clock jump
    h.clock_t[0] += 0
    real = h.g.run_sleep
    h.g.run_sleep = lambda c: (real(c), h.nap(1000))
    h.logs.clear()
    h.g.tick()
    assert not any("did not sleep" in m for m in h.logs)


def test_missing_sysfs_is_harmless(tmp_path):
    g = PowerGuard(
        PowerConfig(),
        battery=tmp_path / "nope",
        ac=tmp_path / "nope2",
        sleep=lambda s: None,
        log=lambda m: None,
    )
    assert g.tick() == 300 and Path(tmp_path).exists()
