"""Wait for a calm network, and pause bulk jobs while it is not.

``awb calm wait 10 && awb queue run ...`` starts a download only after the network has been quiet for ten minutes.
``awb netguard -- CMD`` runs a command that cannot pace itself and stops it (SIGSTOP, the whole process group) as soon
as the network turns busy, then lets it continue after a calm period. Both read the same two signals as the pacer:
the round trip to the router and to an internet host.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import NetpaceConfig
from .netpace import Baseline, gateway, ping

Ping = Callable[[str], list[float]]


class CalmDetector:
    """Calm = worst LAN ping under ``calm_lan_ms`` and worst internet ping under baseline + ``calm_wan_slack_ms``."""

    def __init__(self, cfg: NetpaceConfig) -> None:
        self.c, self.base = cfg, Baseline()

    def is_calm(self, lan: list[float], wan: list[float]) -> bool:
        self.base.feed(wan)
        return (
            bool(lan and wan)
            and max(lan) < self.c.calm_lan_ms
            and max(wan) < (self.base.wan or 0) + self.c.calm_wan_slack_ms
        )

    def is_busy(self, lan: list[float], wan: list[float]) -> bool:
        self.base.feed(wan)
        if not lan or not wan:
            return False  # no answer is not evidence of a game: do not stop a job for it
        return max(lan) > self.c.high_ms or max(wan) > (self.base.wan or 0) + self.c.wan_slack_ms


def probe(cfg: NetpaceConfig, ping_fn: Ping = ping) -> tuple[list[float], list[float]]:
    router = cfg.router or gateway()
    return (ping_fn(router) if router else []), ping_fn(cfg.wan)


def wait_calm(
    cfg: NetpaceConfig,
    minutes: float,
    *,
    ping_fn: Ping = ping,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    every: float = 20.0,
    log: Callable[[str], None] = print,
) -> None:
    """Return once the network has been calm for ``minutes`` in a row (a busy reading restarts the count)."""
    det, since = CalmDetector(cfg), None
    while True:
        lan, wan = probe(cfg, ping_fn)
        now = clock()
        since = (since or now) if det.is_calm(lan, wan) else None
        if since is not None and now - since >= minutes * 60:
            log(f"network calm for {minutes:g} min")
            return
        sleep(every)


@dataclass
class PauseGate:
    """The state machine behind ``netguard``: busy -> pause now; calm for ``calm_minutes`` -> resume."""

    cfg: NetpaceConfig
    paused: bool = False
    calm_since: float | None = None

    def __post_init__(self) -> None:
        self.det = CalmDetector(self.cfg)

    def update(self, lan: list[float], wan: list[float], now: float) -> str | None:
        if not self.paused:
            if self.det.is_busy(lan, wan):
                self.paused, self.calm_since = True, None
                return "pause"
            self.det.base.feed(wan)
            return None
        self.calm_since = (self.calm_since or now) if self.det.is_calm(lan, wan) else None
        if self.calm_since is not None and now - self.calm_since >= self.cfg.calm_minutes * 60:
            self.paused = False
            return "resume"
        return None


def netguard(
    cfg: NetpaceConfig,
    argv: list[str],
    *,
    ping_fn: Ping = ping,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    every: float = 5.0,
    log: Callable[[str], None] = print,
) -> int:
    """Run ``argv``; stop it while the network is busy. Returns its exit code."""
    gate = PauseGate(cfg)
    child = subprocess.Popen(argv, start_new_session=True)
    pgid = os.getpgid(child.pid)

    def forward(sig: int, _f: object) -> None:
        try:
            os.killpg(pgid, signal.SIGCONT)
            os.killpg(pgid, sig)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    try:
        while child.poll() is None:
            act = gate.update(*probe(cfg, ping_fn), clock())
            if act == "pause":
                os.killpg(pgid, signal.SIGSTOP)
                log(f"{time.strftime('%T')} network busy: paused {argv[0]}")
            elif act == "resume":
                os.killpg(pgid, signal.SIGCONT)
                log(f"{time.strftime('%T')} network calm: resumed {argv[0]}")
            sleep(every)
    finally:
        if child.poll() is None:
            forward(signal.SIGTERM, None)
    return child.returncode if child.returncode is not None else 1


__all__ = ["CalmDetector", "PauseGate", "netguard", "probe", "wait_calm"]
