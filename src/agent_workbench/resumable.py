"""Run a resumable job until it ends on its own.

A job killed by the memory governor, the OOM killer or a reboot is not a failure if it can continue where it
stopped. ``run_resumable`` reruns the command, waiting before each rerun until memory has been comfortable for a few
polls in a row, and gives up after ``max_stalls`` reruns that made no progress (the log did not grow).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .config import ResumableConfig


def free_gb() -> float:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / 1048576
    except OSError:
        pass
    return 99.0


def _lines(p: Path) -> int:
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def run_resumable(
    argv: Sequence[str],
    log: Path,
    cfg: ResumableConfig,
    *,
    runner: Callable[..., int] | None = None,
    free: Callable[[], float] = free_gb,
    sleep: Callable[[float], None] = time.sleep,
    stamp: Callable[[], str] = lambda: time.strftime("%F %T", time.gmtime()),
) -> int:
    """Returns 0 when the job finishes cleanly, else the last exit code after giving up."""
    log.parent.mkdir(parents=True, exist_ok=True)

    def note(msg: str) -> None:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{stamp()} {msg}\n")

    def default_runner(cmd: Sequence[str]) -> int:
        with open(log, "ab") as f:
            return subprocess.run(list(cmd), stdout=f, stderr=subprocess.STDOUT).returncode

    run = runner or default_runner
    stalls = 0
    while True:
        before = _lines(log)
        note(f"run: {' '.join(argv)}")
        rc = run(argv)
        if rc == 0:
            note("done")
            return 0
        grew = _lines(log) > before + 1 + cfg.progress_lines  # beyond our own 'run:' line
        stalls = 0 if grew else stalls + 1
        if stalls >= cfg.max_stalls:
            note(f"stopped: {cfg.max_stalls} restarts without progress (exit {rc})")
            return rc
        note(f"ended with exit {rc}: waiting for memory before going on")
        ok = 0
        while ok < cfg.calm_polls:
            sleep(cfg.poll_seconds)
            ok = ok + 1 if free() >= cfg.resume_free_gb else 0
