"""The remark watcher: prints one line per *settled* remark, so an agent can act on each as it comes.

People type into a box that saves as they go, so a remark is reported only once it has been unchanged for
``settle_seconds``. The read position is saved, so a restarted watcher loses nothing and repeats nothing. Run it
as a Claude Code Monitor (each stdout line is a notification) or pipe it into any agent loop.

One workspace has one stream of remarks and one saved position, so one watcher streams it at a time
(:func:`take_lock`): a second one says who holds the stream and leaves, instead of repeating every line.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from .store import has_content


class Watcher:
    def __init__(
        self,
        remarks: Path,
        pos_file: Path,
        settle: float = 45.0,
        from_start: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.f, self.pos_file, self.settle, self.clock = remarks, pos_file, settle, clock
        size = remarks.stat().st_size if remarks.exists() else 0
        try:
            start = min(int(pos_file.read_text().strip()), size)
        except (OSError, ValueError):
            start = 0 if from_start else size
        self.pos = start
        self.saved: int | None = None  # the position last written to pos_file
        self.seen: dict[
            tuple[str, str], tuple[str, str]
        ] = {}  # (report, card) -> (verdict, comment) already reported
        self.pending: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
        if start and remarks.exists():  # what was said before the start point counts as reported
            with open(remarks, "rb") as fh:
                for ln in fh.read(start).decode("utf-8", "replace").splitlines():
                    r = self._parse(ln)
                    if r:
                        self.seen[(str(r.get("report")), str(r["card"]))] = (
                            r.get("verdict", ""),
                            r.get("comment", ""),
                        )

    @staticmethod
    def _parse(line: str) -> dict[str, Any] | None:
        try:
            r = json.loads(line)
        except ValueError:
            return None
        return r if isinstance(r, dict) and r.get("card") else None

    def poll(self) -> list[dict[str, Any]]:
        """Read new lines, return the remarks that have settled since the last call."""
        if self.f.exists():
            size = self.f.stat().st_size
            if size < self.pos:  # the file was replaced or truncated: start again
                self.pos = 0
            if size > self.pos:
                with open(self.f, "rb") as fh:
                    fh.seek(self.pos)
                    data = fh.read()
                cut = data.rfind(b"\n") + 1  # a half-written last line waits for its newline
                self.pos += cut
                now = self.clock()
                for ln in data[:cut].decode("utf-8", "replace").splitlines():
                    r = self._parse(ln)
                    if r:
                        self.pending[(str(r.get("report")), str(r["card"]))] = (r, now)
        now, out = self.clock(), []
        for k, (r, t) in list(self.pending.items()):
            if now - t >= self.settle:
                del self.pending[k]
                v = (r.get("verdict", ""), r.get("comment", ""))
                if self.seen.get(k) != v and (has_content(r) or k in self.seen):
                    self.seen[k] = v
                    out.append(r)
        if not self.pending and self.pos != self.saved and (self.pos or self.pos_file.parent.exists()):
            try:  # only when it moved: an idle watcher writes nothing, and makes no workspace where none is
                self.pos_file.parent.mkdir(parents=True, exist_ok=True)
                self.pos_file.write_text(str(self.pos))
                self.saved = self.pos
            except OSError:
                pass
        return out

    def drain(self) -> list[dict[str, Any]]:
        """Report everything pending now, settled or not (``awb watch --once``)."""
        self.settle = 0
        return self.poll()


class WatcherBusy(RuntimeError):
    """Another watcher already streams this workspace."""


def take_lock(path: Path) -> IO[str] | None:
    """Hold the workspace's watch lock for the life of the returned file (None where locks do not exist).

    Raises :class:`WatcherBusy` with the holder's pid when another process has it. The lock is advisory and
    dies with its process, so a killed watcher never blocks the next one."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")  # noqa: SIM115 - kept open: the lock lives as long as the file
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        pid = fh.read().strip() or "?"
        fh.close()
        raise WatcherBusy(pid) from None
    except OSError:  # a filesystem without locks: watch anyway
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def format_line(r: dict[str, Any]) -> str:
    """One line, no newlines inside: REMARK <report> <card> verdict=<approve|reject|none> «comment»"""
    c = " ".join(str(r.get("comment", "")).split())
    kind = "REPLY" if r.get("report") == "_news" else "REMARK"
    return f"{kind} {r.get('report')} {r.get('card')} verdict={r.get('verdict') or 'none'}" + (
        f" «{c}»" if c else ""
    )


def run(
    w: Watcher,
    emit: Callable[[str], None],
    as_json: bool = False,
    interval: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    stop: Callable[[], bool] = lambda: False,
) -> None:
    while not stop():
        for r in w.poll():
            emit(json.dumps(r, ensure_ascii=False) if as_json else format_line(r))
        sleep(interval)
