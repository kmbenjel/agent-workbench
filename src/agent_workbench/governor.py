"""Memory governor: keeps a long job from taking the desktop (or the VM) down.

Long agent jobs start batch work (ffmpeg, OCR, builds, headless browsers) beside the person's own applications.
When memory runs short the kernel's OOM killer, or systemd-oomd, kills by reclaim pressure and takes the session
with it. The governor acts first, in a fixed order, with the least destructive step that helps:

1. **Pen** (optional, systemd): a user slice holds the jobs; its ``MemoryHigh`` follows what the person leaves
   free, so the jobs are reclaimed and swapped (slower) before anything else is.
2. **Workers**: a file (``workers``) holds how many units of work a job may run at once, sized from the pen. Jobs
   read it before starting each unit; fewer at once, more only after a calm period, none more for a while after a brake.
3. **Guard**: under pressure the *largest* eligible heavy process is paused (SIGSTOP: it keeps its memory but stops
   growing); under heavy pressure it is killed; when memory is calm again the paused ones continue, one at a time.
4. **Browsers** (opt in): idle browsers are closed the normal way (SIGTERM) before workers are cut.

Never touched: processes in protected units or matching protected command lines (the desktop, editors, the
governor itself). The logic is ``Governor.step()``; everything it reads or does goes through a ``System`` object, so
it is tested with fake processes.
"""

from __future__ import annotations

import glob
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Config, GovernorConfig


@dataclass
class Proc:
    pid: int
    name: str
    rss_mb: float
    cmd: str
    unit: str = ""
    headless: bool = False


class System(Protocol):
    def free_gb(self) -> float: ...
    def pressure(self) -> float: ...
    def procs(self) -> list[Proc]: ...
    def signal(self, pid: int, sig: int) -> bool: ...
    def alive(self, pid: int) -> bool: ...
    def pen_gb(self) -> float: ...
    def size_pen(self, gb: float) -> None: ...
    def idle_minutes(self) -> float: ...
    def sound_playing(self) -> bool: ...
    def browsers(self) -> list[int]: ...


class LinuxSystem:
    """/proc, /sys/fs/cgroup and systemd. Anything unreadable counts as 'plenty of memory, nobody idle'."""

    def __init__(self, slice_name: str = "") -> None:
        self.slice = slice_name
        uid = os.getuid()
        self.cg = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/{slice_name}")

    def free_gb(self) -> float:
        try:
            for ln in Path("/proc/meminfo").read_text().splitlines():
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1]) / 1048576
        except OSError:
            pass
        return 99.0

    def pressure(self) -> float:
        """PSI 'full avg10' in percent: the share of time every task waited on memory (what systemd-oomd watches)."""
        try:
            return float(Path("/proc/pressure/memory").read_text().split("full ")[1].split()[0].split("=")[1])
        except (OSError, IndexError, ValueError):
            return 0.0

    def procs(self) -> list[Proc]:
        out = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                st = {
                    k: v.strip()
                    for k, v in (
                        ln.split(":", 1)
                        for ln in Path(f"/proc/{pid}/status").read_text().splitlines()
                        if ":" in ln
                    )
                }
                rss = int(st.get("VmRSS", "0 kB").split()[0]) / 1024
                cmd = (
                    Path(f"/proc/{pid}/cmdline")
                    .read_bytes()
                    .replace(b"\0", b" ")
                    .decode(errors="ignore")
                    .strip()
                )
                cg = Path(f"/proc/{pid}/cgroup").read_text().strip().rsplit("/", 1)[-1]
            except (OSError, ValueError, IndexError):
                continue
            name = st.get("Name", "")
            out.append(
                Proc(
                    int(pid),
                    name,
                    rss,
                    cmd,
                    cg,
                    name in ("chrome", "chromium", "headless_shell") and "--headless" in cmd,
                )
            )
        return out

    def signal(self, pid: int, sig: int) -> bool:
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False

    def alive(self, pid: int) -> bool:
        return os.path.exists(f"/proc/{pid}")

    def pen_gb(self) -> float:
        if not self.slice:
            return 0.0
        try:
            return int((self.cg / "memory.current").read_text()) / 1073741824
        except (OSError, ValueError):
            return 0.0

    def size_pen(self, gb: float) -> None:
        if not self.slice:
            return
        hi, mx = int(gb * 1024), int((gb + 1.5) * 1024)
        try:  # a busy systemd can take long: a timeout must not kill the governor
            subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "set-property",
                    "--runtime",
                    self.slice,
                    f"MemoryHigh={hi}M",
                    f"MemoryMax={mx}M",
                ],
                capture_output=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    def idle_minutes(self) -> float:
        try:
            out = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.gnome.Mutter.IdleMonitor",
                    "--object-path",
                    "/org/gnome/Mutter/IdleMonitor/Core",
                    "--method",
                    "org.gnome.Mutter.IdleMonitor.GetIdletime",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            return int(out.split()[1].rstrip(",)")) / 60000
        except Exception:
            return 0.0

    def sound_playing(self) -> bool:
        try:
            return any(
                "RUNNING" in Path(f).read_text() for f in glob.glob("/proc/asound/card*/pcm*p/sub*/status")
            )
        except OSError:
            return False

    def browsers(self) -> list[int]:
        return [
            p.pid
            for p in self.procs()
            if p.name in ("brave", "chrome", "firefox", "chromium") and "--type=" not in p.cmd
        ]


class Governor:
    def __init__(
        self,
        cfg: GovernorConfig,
        system: System,
        workers_file: Path,
        paused_file: Path,
        clock: Callable[[], float] = time.time,
        log: Callable[[str], None] | None = None,
        self_pid: int | None = None,
    ) -> None:
        self.c, self.sys, self.wf, self.pf, self.clock = cfg, system, workers_file, paused_file, clock
        self.log = log or (lambda m: print(time.strftime("%F %T"), m, flush=True))
        self.self_pid = os.getpid() if self_pid is None else self_pid
        self.pen: float | None = None
        self.paused: set[int] = {p for p in self._load() if system.alive(p)}
        self.up_since = self.calm_since = self.low_since = None
        self.braked = self.closed_at = self.resumed_at = 0.0
        self._protect = [re.compile(p) for p in cfg.protect_cmd]
        self._jobs = [re.compile(p) for p in cfg.job_patterns]

    # -- persistence -----------------------------------------------------------------------------------------
    def _load(self) -> set[int]:
        try:
            return {int(x) for x in self.pf.read_text().split()}
        except (OSError, ValueError):
            return set()

    def _save(self) -> None:
        try:
            self.pf.parent.mkdir(parents=True, exist_ok=True)
            self.pf.write_text("".join(f"{p}\n" for p in sorted(self.paused)))
        except OSError:
            pass

    def workers(self) -> int:
        try:
            return max(1, min(self.c.max_workers, int(self.wf.read_text().strip())))
        except (OSError, ValueError):
            return self.c.max_workers

    def _put_workers(self, n: int, why: str) -> None:
        old = self.workers()
        try:
            self.wf.parent.mkdir(parents=True, exist_ok=True)
            self.wf.write_text(f"{n}\n")
        except OSError:
            pass
        if n != old:
            self.log(f"workers {old} -> {n}: {why}")

    # -- who may be paused or killed ---------------------------------------------------------------------------
    def eligible(self) -> list[Proc]:
        """Heavy batch processes, largest first. Protected units, protected command lines and ourselves never."""
        out = []
        for p in self.sys.procs():
            if p.pid == self.self_pid or p.rss_mb < self.c.pause_mb:
                continue
            if any(p.unit.startswith(u) for u in self.c.protect_units) or any(
                r.search(p.cmd) for r in self._protect
            ):
                continue
            if p.name in self.c.heavy_names or p.headless or any(r.search(p.cmd) for r in self._jobs):
                out.append(p)
        return sorted(out, key=lambda p: -p.rss_mb)

    def _kill_largest(self, why: str) -> bool:
        for p in self.eligible():
            if self.sys.signal(p.pid, signal.SIGKILL):
                self.log(f"GUARD: {why}: killed pid {p.pid} ({p.rss_mb:.0f} MB) {p.name} {p.cmd[:80]}")
                self.paused.discard(p.pid)
                self._save()
                return True
        return False

    def _pause_largest(self, why: str) -> None:
        for p in self.eligible():
            if p.pid in self.paused:
                continue
            if self.sys.signal(p.pid, signal.SIGSTOP):
                self.paused.add(p.pid)
                self._save()
                self.log(f"GUARD: {why}: paused pid {p.pid} ({p.rss_mb:.0f} MB) {p.name} {p.cmd[:80]}")
                return

    def resume_all(self) -> None:
        for p in sorted(self.paused):
            self.sys.signal(p, signal.SIGCONT)
        self.paused.clear()
        self._save()

    # -- one round ---------------------------------------------------------------------------------------------
    def step(self) -> float:
        """Look, act, and return how many seconds to wait before the next round (shorter when memory is short)."""
        c, now = self.c, self.clock()
        f, ps, held = self.sys.free_gb(), self.sys.pressure(), self.sys.pen_gb()
        self.paused = {p for p in self.paused if self.sys.alive(p)}
        why = f"{f:.1f} GB free, pressure {ps:.0f} %"
        killed = False
        if f < c.kill_gb or ps >= c.kill_pressure:
            killed = self._kill_largest(why)
        elif f < c.warn_gb or ps >= c.warn_pressure:
            self._pause_largest(why)
            self.calm_since = None
        if self.paused and f >= c.calm_gb and ps < c.calm_pressure:
            self.calm_since = self.calm_since or now
            if (
                now - self.calm_since >= c.resume_after_seconds
                and now - self.resumed_at >= c.resume_gap_seconds
            ):
                pid = min(self.paused)
                self.sys.signal(pid, signal.SIGCONT)
                self.log(f"GUARD: {why}, calm: resumed pid {pid}")
                self.paused.discard(pid)
                self._save()
                self.resumed_at = now
        elif self.paused:
            self.calm_since = None
        if (
            f < c.crit_gb or ps >= c.crit_pressure
        ):  # the brake: fewer workers at once, and the biggest goes if not yet
            if not killed:
                self._kill_largest(why)
            self._put_workers(max(1, self.workers() - 2), f"{why} (critical)")
            self.up_since, self.braked = None, now
            return 2
        want = (
            round(min(c.pen_max_gb, max(c.pen_min_gb, f + held - c.keep_gb)) * 4) / 4
        )  # in quarters of a GB
        if self.pen is None or abs(want - self.pen) >= 0.25:
            self.sys.size_pen(want)
            if self.pen is None or abs(want - self.pen) >= 1:
                self.log(f"pen {self.pen} -> {want} GB (free {f:.1f} GB, the jobs hold {held:.1f} GB)")
            self.pen = want
        target = max(1, min(c.max_workers, int((self.pen - c.parent_gb) / c.per_worker_gb)))
        n = self.workers()
        if target < n:
            self._put_workers(target, f"pen {self.pen} GB")
            self.up_since = None
        elif target > n and now - self.braked < c.hold_after_brake_seconds:
            self.up_since = None  # no climb for a while after a brake
        elif target > n:
            self.up_since = self.up_since or now
            if now - self.up_since >= c.grow_after_seconds:
                self._put_workers(n + 1, f"pen {self.pen} GB for {c.grow_after_seconds:.0f} s")
                self.up_since = now
        else:
            self.up_since = None
        if (
            c.close_idle_browsers and f < c.warn_gb + 0.5
        ):  # the person's own apps press: idle browsers go first
            self.low_since = self.low_since or now
            br = self.sys.browsers()
            if br and now - self.closed_at > 600 and now - self.low_since >= 30:
                im = self.sys.idle_minutes()
                if im >= c.idle_minutes and not self.sys.sound_playing():
                    for pid in br:
                        self.sys.signal(pid, signal.SIGTERM)
                        self.log(f"{why}, idle {im:.0f} min: closed browser pid {pid}")
                    self.closed_at = now
        else:
            self.low_since = None
        return 2 if (f < 4 or ps >= 3 or self.paused) else 5


def run(cfg: Config, once: bool = False) -> int:
    g = Governor(
        cfg.governor,
        LinuxSystem(cfg.governor.pen_slice),
        cfg.ws / cfg.governor.workers_file,
        cfg.ws / "paused",
    )

    def bye(*_: object) -> None:
        g.resume_all()  # never leave a process stopped behind
        sys.exit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    g.log(
        f"governor up: <= {g.c.max_workers} workers, pause at {g.c.warn_gb} GB free, kill at {g.c.kill_gb} GB (pid {os.getpid()})"
    )
    while True:
        wait = g.step()
        if once:
            return 0
        time.sleep(wait)
