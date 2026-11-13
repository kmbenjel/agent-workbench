"""Battery guard: a dying battery must not kill a long job.

On battery at or below ``limit_pct`` (two readings in a row) the matching jobs are paused (SIGSTOP, so nothing is
lost) and, when ``sleep_cmd`` is set, the machine sleeps; when the charger is back the jobs continue. A sleep counts
only if the wall clock really jumped by ``held_seconds``: some laptops return from suspend within seconds at a low
battery and still report success, so the exit code proves nothing.
"""

from __future__ import annotations

import re
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .config import PowerConfig


def read_int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


class PowerGuard:
    def __init__(
        self,
        cfg: PowerConfig,
        *,
        find_pids: Callable[[list[re.Pattern[str]]], list[int]] | None = None,
        send: Callable[[int, int], None] | None = None,
        run_sleep: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
        battery: Path | None = None,
        ac: Path | None = None,
    ) -> None:
        self.c = cfg
        self.bat, self.ac = battery or Path(cfg.battery), ac or Path(cfg.ac)
        self.pats = [re.compile(p) for p in cfg.job_patterns]
        self.find_pids = find_pids or self._find
        self.send = send or self._send
        self.run_sleep = run_sleep or (lambda cmd: subprocess.run(cmd, shell=True, check=False))
        self.clock, self.sleep, self.log = clock, sleep, log
        self.low, self.paused, self.stopped = 0, False, []

    @staticmethod
    def _find(pats: list[re.Pattern[str]]) -> list[int]:
        out = []
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
                except OSError:
                    continue
                if any(p.search(cmd) for p in pats):
                    out.append(int(d.name))
        return out

    @staticmethod
    def _send(pid: int, sig: int) -> None:
        try:
            import os

            os.kill(pid, sig)
        except OSError:
            pass

    def on_ac(self) -> bool:
        return read_int(self.ac / "online") == 1

    def percent(self) -> int | None:
        return read_int(self.bat / "capacity")

    def pause_jobs(self) -> None:
        self.stopped = [p for p in self.find_pids(self.pats)]
        for p in self.stopped:
            self.send(p, signal.SIGSTOP)
        self.paused = True
        self.log(f"paused pids {self.stopped}")

    def resume_jobs(self) -> None:
        for p in self.stopped:
            self.send(p, signal.SIGCONT)
        self.log(f"resumed pids {self.stopped}")
        self.stopped, self.paused = [], False

    def sleep_held(self) -> bool:
        """Sleep, then check the wall clock really jumped."""
        t0 = self.clock()
        self.run_sleep(self.c.sleep_cmd)
        self.sleep(20)
        gap = self.clock() - t0 - 20
        self.log(f"slept {gap:.0f} s")
        return gap >= self.c.held_seconds

    def tick(self) -> float:
        """One reading; returns the seconds to wait before the next."""
        pct = self.percent()
        if self.on_ac():
            self.low = 0
            if self.paused:
                self.resume_jobs()
        elif pct is not None and pct <= self.c.limit_pct:
            self.low += 1
            if self.low >= 2:
                if not self.paused:
                    self.log(f"battery {pct} %: pausing jobs")
                    self.sleep(self.c.warn_seconds)  # time to plug in
                    if self.on_ac():
                        return 1
                    self.pause_jobs()
                if self.c.sleep_cmd and not self.sleep_held():
                    self.log("did not sleep: trying again at the next reading")
        else:
            self.low = 0
        return self.c.near_poll_seconds if pct is not None and pct <= self.c.near_pct else self.c.poll_seconds

    def run(self) -> None:
        self.log(f"battery guard on: pause at <= {self.c.limit_pct} %")
        while True:
            self.sleep(self.tick())
