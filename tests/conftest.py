from __future__ import annotations

import pytest

from agent_workbench.config import Config, parse


@pytest.fixture
def cfg(tmp_path) -> Config:
    return parse({}, tmp_path)


@pytest.fixture
def report_cards():
    return {
        "title": "Test report",
        "sections": [{"id": "a", "title": "Section A"}],
        "cards": [
            {"id": "c1", "section": "a", "title": "First <b>", "body": "one\n\ntwo"},
            {"id": "c2", "section": "a", "title": "Second", "body": "x"},
            {"id": "c3", "title": "Third"},
        ],
    }


def start_backend(origins: str, tmp_path):
    """Start the comment backend of examples/cloudflare-worker for a test; returns (process, base URL).

    By default Node runs the Worker's code with a bucket in memory. With ``AWB_WRANGLER`` set to a wrangler
    command (``AWB_WRANGLER="npx wrangler"``) the same tests run against ``wrangler dev``: the real Workers
    runtime and its local R2.
    """
    import os
    import re
    import shlex
    import shutil
    import socket
    import subprocess
    import time
    import urllib.request
    from pathlib import Path

    worker = Path(__file__).resolve().parents[1] / "examples" / "cloudflare-worker"
    keys = {"REVIEW_KEY": "rk", "AGENT_TOKEN": "at", "ALLOWED_ORIGINS": origins}
    wrangler = os.environ.get("AWB_WRANGLER")
    if wrangler:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        argv = [*shlex.split(wrangler), "dev", "--ip", "127.0.0.1", "--port", str(port)]
        argv += ["--persist-to", str(tmp_path / "wrangler-state"), "--show-interactive-dev-session=false"]
        for k, v in keys.items():
            argv += ["--var", f"{k}:{v}"]
        env = {
            **os.environ,
            "CI": "1",
            "WRANGLER_SEND_METRICS": "false",
            "WRANGLER_LOG_PATH": str(tmp_path / "wlog"),
        }
        p = subprocess.Popen(
            argv,
            cwd=worker,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        base = f"http://127.0.0.1:{port}"
        for _ in range(300):
            try:
                urllib.request.urlopen(base + "/", timeout=2).read()
                return p, base
            except OSError:
                time.sleep(0.2)
        p.kill()
        raise RuntimeError("wrangler dev did not start")
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node (18+)")
    p = subprocess.Popen(
        [node, str(worker / "dev-server.mjs"), "--port", "0"], stdout=subprocess.PIPE, text=True, env=keys
    )
    return p, re.search(r"http://127\.0\.0\.1:\d+", p.stdout.readline()).group(0)


def stop_backend(p) -> None:
    import os
    import signal

    try:  # wrangler runs the runtime as a child: stop the whole group
        os.killpg(os.getpgid(p.pid), signal.SIGTERM) if os.environ.get("AWB_WRANGLER") else p.kill()
    except (OSError, AttributeError):
        p.kill()
    p.wait()
