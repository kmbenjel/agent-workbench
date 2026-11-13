"""A resumable download queue: download -> verify -> deliver, with caps on what sits in staging.

Input is a TSV, one item per line: ``url<TAB>size<TAB>target<TAB>reason`` (size 0 = unknown, ``#`` starts a comment).
Stages overlap: ``jobs`` downloads run at once (aria2c with several connections per file, or curl, or plain Python),
each file is verified, and one delivery thread moves it to its destination (a directory, ``rclone``, or your own
command) under an optional lock file. Staged bytes never exceed ``stage_gb`` and the disk is never filled below
``min_free_gb``. A finished item is recorded in ``<workspace>/queue/<name>.done`` and a failed one in ``.failed`` with
the reason, so running the same queue again continues where it stopped.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import shlex
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, QueueConfig


class QueueError(RuntimeError):
    pass


@dataclass
class Item:
    url: str
    size: int
    target: str
    reason: str = ""


def read_queue(path: Path) -> list[Item]:
    items = []
    with open(path, encoding="utf-8", newline="") as f:
        for ln, r in enumerate(csv.reader(f, delimiter="\t"), 1):
            if not r or r[0].startswith("#"):
                continue
            if len(r) < 3:
                raise QueueError(
                    f"{path}:{ln}: expected url<TAB>size<TAB>target[<TAB>reason], got {len(r)} column(s)"
                )
            try:
                size = int(r[1] or 0)
            except ValueError as e:
                raise QueueError(f"{path}:{ln}: size {r[1]!r} is not a number") from e
            items.append(Item(r[0].strip(), size, safe_target(r[2]), r[3] if len(r) > 3 else ""))
    return items


def safe_target(t: str) -> str:
    """A target is a relative path with no '..': nothing a queue file says can write outside the destination."""
    p = Path(t)
    if not t.strip() or p.is_absolute() or ".." in p.parts:
        raise QueueError(f"unsafe target {t!r}: must be a relative path without '..'")
    return str(p)


# -- verification -----------------------------------------------------------------------------------------------
def verify_file(
    p: Path,
    size: int,
    c: QueueConfig,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str | None:
    """None if the file is fine, else why not."""
    try:
        n = p.stat().st_size
        if c.verify_size and size and n != size:
            return f"size {n} != {size}"
        if c.verify_magic or c.verify_tail:
            with open(p, "rb") as f:
                if c.verify_magic and f.read(len(c.verify_magic.encode())) != c.verify_magic.encode():
                    return f"does not start with {c.verify_magic!r}"
                if c.verify_tail:
                    f.seek(max(0, n - 8192))
                    if c.verify_tail.encode() not in f.read():
                        return f"no {c.verify_tail!r} near the end"
        if c.verify_cmd:
            argv = [a.replace("{file}", str(p)) for a in shlex.split(c.verify_cmd)]
            if run(argv, capture_output=True).returncode:
                return f"{argv[0]} rejects the file"
    except OSError as e:
        return str(e)
    return None


# -- downloaders ------------------------------------------------------------------------------------------------
def download_url(
    url: str,
    out: Path,
    c: QueueConfig,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("file://"):
        shutil.copyfile(url[7:], out)
        return
    kind = c.downloader
    if kind == "auto":
        kind = "aria2c" if shutil.which("aria2c") else "curl" if shutil.which("curl") else "python"
    cap = c.net_mb_s * 1e6 / max(1, c.jobs) if c.net_mb_s else 0
    if kind == "aria2c":
        cmd = [
            "aria2c",
            "-q",
            "--continue=true",
            f"-x{c.connections}",
            f"-s{c.connections}",
            "-k1M",
            "--max-tries=5",
            "--retry-wait=10",
            "--allow-overwrite=true",
            "--auto-file-renaming=false",
            "-U",
            c.user_agent,
            "-d",
            str(out.parent),
            "-o",
            out.name,
            url,
        ]
        if cap:
            cmd.insert(2, f"--max-download-limit={int(cap)}")
        r = run(cmd, capture_output=True)
        if r.returncode == 0:
            return
        out.unlink(missing_ok=True)  # some servers refuse ranged requests: one plain stream
        kind = "curl" if shutil.which("curl") else "python"
    if kind == "curl":
        cmd = ["curl", "-sfL", "--retry", "5", "--retry-delay", "10", "-A", c.user_agent, "-o", str(out), url]
        if cap:
            cmd[1:1] = ["--limit-rate", str(int(cap))]
        r = run(cmd, capture_output=True)
        if r.returncode:
            raise QueueError(f"curl exit {r.returncode}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": c.user_agent})
    with urllib.request.urlopen(req, timeout=60) as resp, open(out, "wb") as f:
        shutil.copyfileobj(resp, f)


# -- delivery ---------------------------------------------------------------------------------------------------
class FileLock:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self) -> FileLock:
        self.f = None
        if self.path:
            self.f = open(self.path, "w")
            fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a: object) -> None:
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()


def deliver_file(
    src: Path,
    target: str,
    c: QueueConfig,
    base: Path,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    if c.deliver == "local":
        dst = Path(c.deliver_to)
        dst = (dst if dst.is_absolute() else base / dst) / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
    elif c.deliver == "rclone":
        remote = f"{c.deliver_to.rstrip('/')}/{target}"
        r = run(["rclone", "copyto", str(src), remote, "-q", "--retries", "5"], capture_output=True)
        if r.returncode:
            raise QueueError("rclone: " + r.stderr.decode(errors="replace")[-150:])
    elif c.deliver == "command":
        if not c.deliver_cmd:
            raise QueueError("[queue] deliver = command needs deliver_cmd")
        argv = [a.replace("{src}", str(src)).replace("{dst}", target) for a in shlex.split(c.deliver_cmd)]
        r = run(argv, capture_output=True)
        if r.returncode:
            raise QueueError(f"{argv[0]} exit {r.returncode}")
    else:
        raise QueueError(f"unknown [queue] deliver {c.deliver!r}: local, rclone or command")


@dataclass
class Stats:
    ok: int = 0
    bad: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


class DownloadQueue:
    def __init__(
        self,
        cfg: Config,
        name: str,
        *,
        fetch: Callable[[str, Path], None] | None = None,
        deliver: Callable[[Path, str], None] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.cfg, self.c, self.name, self.log = cfg, cfg.queue, name, log
        qd = cfg.ws / "queue"
        qd.mkdir(parents=True, exist_ok=True)
        self.done_f, self.failed_f = qd / f"{name}.done", qd / f"{name}.failed"
        self.done = (
            set(self.done_f.read_text(encoding="utf-8").splitlines()) if self.done_f.exists() else set()
        )
        stage = Path(self.c.stage_dir)
        self.stage = stage if stage.is_absolute() else cfg.ws / stage
        self.stage.mkdir(parents=True, exist_ok=True)
        self.fetch = fetch or (lambda url, out: download_url(url, out, self.c))
        self.deliver = deliver or (lambda src, target: deliver_file(src, target, self.c, cfg.root))
        self.lock, self.cv = threading.Lock(), threading.Condition()
        self.staged = 0
        self.stats = Stats()

    # state ------------------------------------------------------------------------------------------------------
    def _ok(self, item: Item) -> None:
        with self.lock:
            self.done.add(item.target)
            with open(self.done_f, "a", encoding="utf-8") as f:
                f.write(item.target + "\n")
            self.stats.ok += 1
            self.log(f"[{self.stats.ok} ok / {self.stats.bad} failed] {item.target[-100:]}")

    def _fail(self, item: Item, why: str) -> None:
        with self.lock:
            self.stats.bad += 1
            self.stats.failures.append((item.target, why))
            with open(self.failed_f, "a", encoding="utf-8") as f:
                f.write(f"{item.target}\t{why}\n")
            self.log(f"FAIL {why}: {item.target[-90:]}")

    def _wait_disk(self) -> None:
        while shutil.disk_usage(self.stage).free < self.c.min_free_gb * 1e9:
            time.sleep(30)

    def _release(self, size: int) -> None:
        with self.cv:
            self.staged -= size
            self.cv.notify_all()

    # stages -----------------------------------------------------------------------------------------------------
    def _download(self, item: Item) -> tuple[Item, Path] | None:
        if item.target in self.done:
            self.stats.skipped += 1
            return None
        self._wait_disk()
        reserve = item.size
        with self.cv:  # the staging cap: a big file waits until others have been delivered (never deadlocks on an empty stage)
            while self.staged + reserve > self.c.stage_gb * 1e9 and self.staged > 0:
                self.cv.wait()
            self.staged += reserve
        out = self.stage / (hashlib.sha1(item.target.encode()).hexdigest() + ".dl")
        try:
            self.fetch(item.url, out)
            why = verify_file(out, item.size, self.c)
        except Exception as e:  # noqa: BLE001 - any failure is recorded against the item, the queue goes on
            why = f"{type(e).__name__}: {e}"[:200]
        if why:
            self._fail(item, why)
            out.unlink(missing_ok=True)
            self._release(reserve)
            return None
        return item, out

    def _deliver(self, item: Item, out: Path) -> None:
        err = ""
        for attempt in range(max(1, self.c.deliver_retries)):
            try:
                with FileLock(self.c.deliver_lock):
                    self.deliver(out, item.target)
                err = ""
                break
            except Exception as e:  # noqa: BLE001
                err = f"deliver: {e}"[:200]
                time.sleep(min(2 * attempt, 10))
        if err:
            self._fail(item, err)  # the staged file is kept: the next run reuses it
        else:
            self._ok(item)
            out.unlink(missing_ok=True)
        self._release(item.size)

    def run(self, items: list[Item]) -> Stats:
        self.log(
            f"{len(items)} items, {sum(i.target in self.done for i in items)} already done; "
            f"{self.c.jobs} downloads x {self.c.connections} connections"
        )
        ready: list[tuple[Item, Path]] = []
        cond = threading.Condition()
        finished = [False]

        def consumer() -> None:
            while True:
                with cond:
                    while not ready and not finished[0]:
                        cond.wait()
                    if not ready:
                        return
                    item, out = ready.pop(0)
                self._deliver(item, out)

        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        def work(item: Item) -> None:
            r = self._download(item)
            if r:
                with cond:
                    ready.append(r)
                    cond.notify()

        with ThreadPoolExecutor(max(1, self.c.jobs)) as ex:
            list(ex.map(work, items))
        with cond:
            finished[0] = True
            cond.notify()
        t.join()
        self.log(f"DONE ok={self.stats.ok} failed={self.stats.bad} skipped={self.stats.skipped}")
        return self.stats
