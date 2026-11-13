#!/usr/bin/env python3
"""Reproducible footprint comparison: install size, package count, and time until a review UI answers HTTP.

    python bench/compare.py [--keep] [--skip label-studio,argilla]

Each tool is installed from PyPI (agent-workbench: the local wheel in dist/ unless --pypi) into a throw-away venv made
with uv. "Time to first page" is the time from the start command to the first HTTP 200 of the tool's own UI; for
agent-workbench that page already holds cards (``awb demo``), for the others it is the empty UI (a project, a
labeling config and tasks still have to be created), so the numbers favour the others. Prints a Markdown table.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def du_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6


def uv_env(base: Path, venv: str = "") -> dict[str, str]:
    """A private uv cache inside the throw-away directory: cold-cache timings, nothing left behind."""
    return {**os.environ, "VIRTUAL_ENV": venv, "UV_CACHE_DIR": str(base / "uv-cache")}


def make_venv(base: Path, name: str) -> Path:
    v = base / name
    subprocess.run(["uv", "venv", "-q", str(v)], check=True, env=uv_env(base))
    return v


def pip(v: Path, *args: str) -> float:
    t = time.time()
    subprocess.run(["uv", "pip", "install", "-q", *args], check=True, env=uv_env(v.parent, str(v)))
    return time.time() - t


def packages(v: Path) -> int:
    out = subprocess.run(["uv", "pip", "list", "--format", "json"], capture_output=True, text=True, env=uv_env(v.parent, str(v))).stdout
    return len(json.loads(out))


def wait_http(url: str, timeout: float) -> float | None:
    t = time.time()
    while time.time() - t < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return time.time() - t
        except Exception:
            time.sleep(0.2)
    return None


def rss_mb(pid: int) -> float:
    total = 0.0
    try:
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    st = (d / "stat").read_text().split(")")[-1].split()
                    if int(d.name) == pid or int(st[1]) == pid:
                        total += int(next(x for x in (d / "status").read_text().splitlines() if x.startswith("VmRSS")).split()[1]) / 1024
                except (OSError, StopIteration, ValueError, IndexError):
                    pass
    except OSError:
        pass
    return total


def bench_awb(base: Path, pypi: bool) -> dict:
    v = make_venv(base, "awb")
    wheel = sorted((REPO / "dist").glob("*.whl"))
    t_install = pip(v, "agent-workbench") if pypi or not wheel else pip(v, str(wheel[-1]))
    port, work = free_port(), base / "awb-work"
    t0 = time.time()
    subprocess.run([str(v / "bin" / "awb"), "demo", str(work), "--port", str(port)], check=True, capture_output=True)
    srv = subprocess.Popen([str(v / "bin" / "awb"), "--root", str(work), "serve", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t = wait_http(f"http://127.0.0.1:{port}/ocr-fixes.html", 30)
        first = None if t is None else time.time() - t0
        mem = rss_mb(srv.pid)
    finally:
        srv.kill()
    return {"tool": "agent-workbench", "install_s": t_install, "disk_mb": du_mb(v), "packages": packages(v), "first_page_s": first, "rss_mb": mem}


def bench_ls(base: Path) -> dict:
    v = make_venv(base, "ls")
    t_install = pip(v, "label-studio")
    port, data = free_port(), base / "ls-data"
    env = {**os.environ, "LABEL_STUDIO_BASE_DATA_DIR": str(data), "DISABLE_SIGNUP_WITHOUT_LINK": "false"}
    t0 = time.time()
    srv = subprocess.Popen(["nice", str(v / "bin" / "label-studio"), "start", "--no-browser", "-p", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        t = wait_http(f"http://127.0.0.1:{port}/health", 300)
        first = None if t is None else time.time() - t0
        time.sleep(2)
        mem = rss_mb(srv.pid)
    finally:
        try:
            os.killpg(srv.pid, 15)
        except OSError:
            pass
    return {"tool": "label-studio", "install_s": t_install, "disk_mb": du_mb(v), "packages": packages(v), "first_page_s": first, "rss_mb": mem}


def bench_argilla(base: Path) -> dict:
    v = make_venv(base, "argilla")
    t_install = pip(v, "argilla")
    return {"tool": "argilla (SDK only)", "install_s": t_install, "disk_mb": du_mb(v), "packages": packages(v), "first_page_s": None, "rss_mb": None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--pypi", action="store_true", help="install agent-workbench from PyPI instead of dist/")
    ap.add_argument("--skip", default="")
    a = ap.parse_args()
    skip = set(a.skip.split(","))
    base = Path(tempfile.mkdtemp(prefix="awb-bench-"))
    rows = []
    try:
        if "agent-workbench" not in skip:
            rows.append(bench_awb(base, a.pypi))
        if "label-studio" not in skip:
            rows.append(bench_ls(base))
        if "argilla" not in skip:
            rows.append(bench_argilla(base))
    finally:
        if not a.keep:
            shutil.rmtree(base, ignore_errors=True)
    f = lambda x, d=0, s="": "n/a" if x is None else f"{x:.{d}f}{s}"  # noqa: E731
    print(f"\nuv {subprocess.run(['uv', '--version'], capture_output=True, text=True).stdout.strip()}, Python {sys.version.split()[0]}, {time.strftime('%F')}\n")
    print("| tool | install time | disk | packages | time to first page | server RSS |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['tool']} | {f(r['install_s'], 1, ' s')} | {f(r['disk_mb'], 1 if r['disk_mb'] < 10 else 0, ' MB')} | {r['packages']} | {f(r['first_page_s'], 1, ' s')} | {f(r['rss_mb'], 0, ' MB')} |")


if __name__ == "__main__":
    main()
