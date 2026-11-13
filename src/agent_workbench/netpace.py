"""Network pacing: bulk transfers must not make other people on the same network lag.

Latency is the signal. A round trip to the router (the Wi-Fi) and one to an internet host are measured every
couple of seconds; when either rises above its quiet baseline someone is gaming or on a call, and the transfer
slows down; when both stay quiet it speeds up again, between a floor and a ceiling.

``Pacer.wait(n)`` is a leaky bucket for code that moves bytes itself (``paced_copy``). For programs that cannot be
paced from inside (aria2c, rclone) use ``awb calm wait`` before starting and ``awb netguard -- CMD`` to pause them
while the network is busy (see calm.py).
"""

from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .config import NetpaceConfig


def gateway() -> str | None:
    """The default gateway from /proc/net/route (Linux); None when unknown."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines():
            f = line.split()
            if len(f) > 2 and f[1] == "00000000" and f[2] != "00000000":
                return socket.inet_ntoa(bytes.fromhex(f[2])[::-1])
    except (OSError, ValueError):
        pass
    return None


def parse_ping(output: str) -> list[float]:
    return [float(x) for x in re.findall(r"time[=<]([\d.]+)", output)]


def ping(host: str, count: int = 4, interval: float = 0.25) -> list[float]:
    try:
        out = subprocess.run(
            ["ping", "-c", str(count), "-i", str(interval), "-W", "1", host],
            capture_output=True,
            text=True,
            timeout=count * 2 + 5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_ping(out)


class Baseline:
    """What 'quiet' looks like on this network: the lowest internet round trip seen, forgetting slowly upward."""

    def __init__(self) -> None:
        self.wan: float | None = None

    def feed(self, wan: list[float]) -> None:
        if wan:
            w = min(wan)
            self.wan = w if self.wan is None else min(self.wan * 1.002, w)


class RateController:
    """Cut the rate by 40 % while the network is busy, grow it 10 % per round while it is calm."""

    def __init__(self, cfg: NetpaceConfig, start_mb_s: float = 2.0) -> None:
        self.c, self.rate, self.base = cfg, start_mb_s * 1e6, Baseline()
        self.lan: float | None = None
        self.wan: float | None = None

    def busy(self, lan: list[float], wan: list[float]) -> bool:
        b = bool(lan) and max(lan) > self.c.high_ms
        return b or (
            bool(wan) and self.base.wan is not None and max(wan) > self.base.wan + self.c.wan_slack_ms
        )

    def calm(self, lan: list[float], wan: list[float]) -> bool:
        return (
            bool(lan)
            and max(lan) < self.c.low_ms
            and (not wan or max(wan) < (self.base.wan or 0) + self.c.wan_slack_ms / 2)
        )

    def update(self, lan: list[float], wan: list[float]) -> float:
        self.base.feed(wan)
        self.lan = max(lan) if lan else None
        self.wan = max(wan) if wan else None
        if self.busy(lan, wan):
            self.rate = max(self.c.min_mb_s * 1e6, self.rate * 0.6)
        elif self.calm(lan, wan):
            self.rate = min(self.c.max_mb_s * 1e6, self.rate * 1.1)
        return self.rate


class Pacer:
    def __init__(
        self,
        cfg: NetpaceConfig,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        probe: bool = False,
    ) -> None:
        self.cfg, self.clock, self.sleep = cfg, clock, sleep
        self.ctl = RateController(cfg)
        self.t, self.sent = clock(), 0.0
        self.lock = threading.Lock()
        if probe and cfg.enabled:
            threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        router = self.cfg.router or gateway()
        while True:
            self.ctl.update(ping(router) if router else [], ping(self.cfg.wan))
            time.sleep(2)

    @property
    def rate(self) -> float:
        return self.ctl.rate

    def wait(self, n: int) -> None:
        """Block so that, on average, no more than ``rate`` bytes per second pass (0.25 s burst allowed)."""
        if not self.cfg.enabled:
            return
        with self.lock:
            now = self.clock()
            self.sent = max(0.0, self.sent - (now - self.t) * self.rate) + n
            self.t = now
            delay = self.sent / self.rate - 0.25
        if delay > 0:
            self.sleep(delay)

    def status(self) -> str:
        if not self.cfg.enabled:
            return "pace off"
        f = lambda v: "?" if v is None else f"{v:.0f}"  # noqa: E731
        return f"pace {self.rate / 1e6:.1f} MB/s, lan {f(self.ctl.lan)} ms, internet {f(self.ctl.wan)}/{f(self.ctl.base.wan)} ms"


def chunks(f, size: int = 1 << 20) -> Iterator[bytes]:
    while buf := f.read(size):
        yield buf


def paced_copy(src: Path, dst: Path, pacer: Pacer, chunk: int = 1 << 20) -> int:
    """Copy a file through the pacer; the destination appears only when complete."""
    tmp = dst.with_name(dst.name + ".part")
    dst.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(src, "rb") as r, open(tmp, "wb") as w:
        for buf in chunks(r, chunk):
            pacer.wait(len(buf))
            w.write(buf)
            n += len(buf)
    tmp.replace(dst)
    return n
