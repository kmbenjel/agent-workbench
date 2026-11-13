"""Remote mode: build the page here, serve it from a cloud machine, reach it through an ssh tunnel.

Why: the reviewer is on a phone or another laptop, the agent's laptop sleeps, and a review page that comes from a
machine nobody else can reach needs no open port and no static address. The remote machine runs only this package's
stdlib server on its own loopback; the local side

* pushes the package, the generated site, the news file and the remarks seen so far (read-only, ``base.jsonl``),
* keeps an ``ssh -N -L`` tunnel (a user unit, restarted by systemd) so the page keeps its local URL,
* pulls the remarks saved there every ``sync_seconds`` into the local ``remarks.jsonl`` (so ``awb watch`` sees them),
* and, when no card is open any more, takes a last pull and stops the machine and the tunnel (``auto_stop``).

Machines come from a *provider* (an always-on ssh host, gcloud, or your own shell commands); everything the provider
and the transport do goes through small classes so the control logic can be tested with fakes.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from .config import Config, RemoteConfig
from .page import build_index, list_reports, open_count

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def sh(
    argv: Sequence[str] | str,
    *,
    input: str | None = None,
    timeout: float | None = 120,
    check: bool = False,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, input=input, capture_output=True, text=True, timeout=timeout, check=check, shell=shell
    )


class RemoteError(RuntimeError):
    pass


# -- providers --------------------------------------------------------------------------------------------------
class Provider(Protocol):
    def status(self) -> str: ...  # RUNNING | STOPPED | MISSING
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def address(self) -> str | None: ...


class StaticProvider:
    """An always-on ssh host: nothing to start, and ``down`` leaves it running."""

    def __init__(self, cfg: RemoteConfig) -> None:
        if not cfg.host:
            raise RemoteError("[remote] host is empty: set it to user@host or an ssh alias")
        self.host = cfg.host

    def status(self) -> str:
        return "RUNNING"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def address(self) -> str | None:
        return self.host


class GcloudProvider:
    def __init__(self, cfg: RemoteConfig, run: Run = sh) -> None:
        if not (cfg.instance and cfg.zone):
            raise RemoteError("[remote] provider gcloud needs instance and zone (and usually project)")
        self.run = run
        self.base = ["gcloud", "compute", "instances"] + (["--project", cfg.project] if cfg.project else [])
        self.tail = ["--zone", cfg.zone]
        self.name = cfg.instance

    def _gc(self, verb: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run([*self.base, verb, self.name, *self.tail, *extra])

    def status(self) -> str:
        r = self._gc("describe", "--format=value(status)")
        return r.stdout.strip() or "MISSING"

    def start(self) -> None:
        if self._gc("start").returncode:
            raise RemoteError(f"could not start {self.name}")

    def stop(self) -> None:
        self._gc("stop")

    def address(self) -> str | None:
        r = self._gc("describe", "--format=value(networkInterfaces[0].accessConfigs[0].natIP)")
        return r.stdout.strip() or None


class CommandProvider:
    """Your own shell commands; ``status_cmd`` prints RUNNING when the machine is up, ``address_cmd`` its host."""

    def __init__(self, cfg: RemoteConfig, run: Run = sh) -> None:
        if not (cfg.status_cmd and cfg.address_cmd):
            raise RemoteError(
                "[remote] provider command needs status_cmd and address_cmd (start_cmd/stop_cmd optional)"
            )
        self.cfg, self.run = cfg, run

    def _sh(self, cmd: str) -> subprocess.CompletedProcess[str]:
        return self.run(cmd, shell=True)

    def status(self) -> str:
        return self._sh(self.cfg.status_cmd).stdout.strip() or "MISSING"

    def start(self) -> None:
        if self.cfg.start_cmd and self._sh(self.cfg.start_cmd).returncode:
            raise RemoteError("start_cmd failed")

    def stop(self) -> None:
        if self.cfg.stop_cmd:
            self._sh(self.cfg.stop_cmd)

    def address(self) -> str | None:
        return self._sh(self.cfg.address_cmd).stdout.strip() or None


def make_provider(cfg: RemoteConfig, run: Run = sh) -> Provider:
    if cfg.provider == "ssh":
        return StaticProvider(cfg)
    if cfg.provider == "gcloud":
        return GcloudProvider(cfg, run)
    if cfg.provider == "command":
        return CommandProvider(cfg, run)
    raise RemoteError(f"unknown [remote] provider {cfg.provider!r}: ssh, gcloud or command")


# -- units: tunnel and sync loop --------------------------------------------------------------------------------
class Units:
    """Long-running helpers as systemd user units when systemd is there, else as detached processes."""

    def __init__(self, cfg: Config, run: Run = sh) -> None:
        self.cfg, self.run = cfg, run
        self.prefix = cfg.remote.unit_prefix
        self.state = cfg.ws / "remote-state.json"
        self.systemd = bool(shutil.which("systemd-run")) and run(
            ["systemctl", "--user", "is-system-running"]
        ).returncode in (0, 1)

    def _name(self, n: str) -> str:
        return f"{self.prefix}-{n}"

    def active(self, n: str) -> bool:
        if self.systemd:
            return self.run(["systemctl", "--user", "is-active", "-q", self._name(n)]).returncode == 0
        pid = self._get(n).get("pid")
        try:
            os.kill(int(pid), 0)
            return True
        except (TypeError, ValueError, OSError):
            return False

    def _all(self) -> dict[str, Any]:
        try:
            return json.loads(self.state.read_text())
        except (OSError, ValueError):
            return {}

    def _get(self, n: str) -> dict[str, Any]:
        return self._all().get(n, {})

    def _put(self, n: str, **kw: Any) -> None:
        d = self._all()
        d[n] = {**d.get(n, {}), **kw}
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(d))

    def target(self, n: str) -> str:
        return str(self._get(n).get("target", ""))

    def start(self, n: str, argv: list[str], target: str = "", restart: bool = False) -> None:
        self.stop(n)
        if self.systemd:
            self.run(["systemctl", "--user", "reset-failed", self._name(n)])
            extra = ["-p", "Restart=always", "-p", "RestartSec=10s"] if restart else []
            r = self.run(
                [
                    "systemd-run",
                    "--user",
                    "-q",
                    f"--unit={self._name(n)}",
                    f"--working-directory={self.cfg.root}",
                    f"--setenv=PATH={os.environ.get('PATH', '')}",
                    *extra,
                    *argv,
                ]
            )
            if r.returncode:
                raise RemoteError(f"could not start unit {self._name(n)}: {r.stderr.strip()}")
            self._put(n, target=target)
        else:
            p = subprocess.Popen(
                argv,
                cwd=self.cfg.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._put(n, pid=p.pid, target=target)

    def stop(self, n: str) -> None:
        if self.systemd:
            self.run(["systemctl", "--user", "stop", self._name(n)])
        else:
            pid = self._get(n).get("pid")
            if pid:
                try:
                    os.killpg(int(pid), 15)
                except (OSError, ValueError):
                    pass
            self._put(n, pid=None)


# -- transport --------------------------------------------------------------------------------------------------
def merge_pulled(local: Path, pulled: Path) -> int:
    """Append the lines of ``pulled`` that ``local`` does not have; returns how many (the order of arrival is kept)."""
    have = set(local.read_text(encoding="utf-8").splitlines()) if local.exists() else set()
    new = [ln for ln in pulled.read_text(encoding="utf-8").splitlines() if ln.strip() and ln not in have]
    if new:
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "a", encoding="utf-8") as f:
            f.write("".join(ln + "\n" for ln in new))
    return len(new)


class Transport(Protocol):
    def reachable(self, addr: str) -> bool: ...
    def push(self, addr: str) -> None: ...
    def pull(self, addr: str) -> int: ...
    def serve(self, addr: str) -> None: ...
    def tunnel_argv(self, addr: str) -> list[str]: ...


class SshTransport:
    def __init__(self, cfg: Config, token: str, run: Run = sh) -> None:
        self.cfg, self.rc, self.token, self.run = cfg, cfg.remote, token, run
        self.rd = cfg.remote.remote_dir

    def _dest(self, addr: str) -> str:
        return f"{self.rc.ssh_user}@{addr}" if self.rc.ssh_user and "@" not in addr else addr

    def _opts(self) -> list[str]:
        o = ["-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes"]
        return o + (["-i", str(Path(self.rc.ssh_key).expanduser())] if self.rc.ssh_key else [])

    def ssh(self, addr: str, cmd: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.run(["ssh", *self._opts(), self._dest(addr), cmd], input=input)

    def rsync(self, *args: str) -> subprocess.CompletedProcess[str]:
        if not shutil.which("rsync"):
            raise RemoteError("rsync is needed on both machines")
        return self.run(
            ["rsync", "-az", "-e", " ".join(shlex.quote(x) for x in ["ssh", *self._opts()]), *args]
        )

    def reachable(self, addr: str) -> bool:
        return self.ssh(addr, "true").returncode == 0

    def push(self, addr: str) -> None:
        cfg, d, rd = self.cfg, self._dest(addr), self.rd
        build_index(cfg)
        pkg = Path(__file__).parent
        steps = [
            ["--delete", "--exclude", "__pycache__", f"{pkg}/", f"{d}:{rd}/pkg/agent_workbench/"],
            ["--delete", f"{cfg.site_dir}/", f"{d}:{rd}/site/"],
        ]
        self.ssh(addr, f"mkdir -p {shlex.quote(rd)}/pkg {shlex.quote(rd)}/site {shlex.quote(rd)}/ws")
        for s in steps:
            r = self.rsync(*s)
            if r.returncode:
                raise RemoteError(f"rsync failed: {r.stderr.strip()[:200]}")
        for local, remote in ((cfg.remarks_file, "base.jsonl"), (cfg.news_file, "ws/news.jsonl")):
            if not local.exists():
                local.parent.mkdir(parents=True, exist_ok=True)
                local.touch()
            r = self.rsync(str(local), f"{d}:{rd}/{remote}.new")
            if r.returncode:
                raise RemoteError(f"rsync failed: {r.stderr.strip()[:200]}")
            self.ssh(addr, f"mv {shlex.quote(rd)}/{remote}.new {shlex.quote(rd)}/{remote}")

    def pull(self, addr: str) -> int:
        tmp = self.cfg.ws / "remote-pull.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        r = self.rsync(f"{self._dest(addr)}:{self.rd}/ws/remarks.jsonl", str(tmp))
        if r.returncode:  # nothing saved there yet
            return 0
        try:
            return merge_pulled(self.cfg.remarks_file, tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def serve(self, addr: str) -> None:
        rd, port = shlex.quote(self.rd), self.rc.port
        self.ssh(addr, f"umask 077; cat > {rd}/env", input=f"AWB_TOKEN={self.token}\n")
        cmd = (
            f"cd {rd} && mkdir -p ws && touch ws/remarks.jsonl && (pkill -f '[a]gent_workbench serve' ; sleep 1; true) && "
            f"set -a && . ./env && set +a && PYTHONPATH=pkg AWB_ROOT=. AWB_WORKSPACE=ws AWB_SITE=site AWB_BASE_REMARKS=base.jsonl "
            f"nohup python3 -m agent_workbench serve --host 127.0.0.1 --port {port} >> server.log 2>&1 < /dev/null & "
            f"sleep 1; pgrep -f '[a]gent_workbench serve' > /dev/null && echo serving"
        )
        r = self.ssh(addr, cmd)
        if "serving" not in r.stdout:
            raise RemoteError(f"the remote server did not start: {(r.stdout + r.stderr).strip()[:300]}")

    def tunnel_argv(self, addr: str) -> list[str]:
        p = self.rc.port
        return [
            "ssh",
            "-N",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ExitOnForwardFailure=yes",
            *self._opts(),
            "-L",
            f"127.0.0.1:{p}:127.0.0.1:{p}",
            self._dest(addr),
        ]


# -- the controller ---------------------------------------------------------------------------------------------
class Remote:
    def __init__(
        self,
        cfg: Config,
        provider: Provider,
        transport: Transport,
        units: Any,
        token: str,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.cfg, self.provider, self.transport, self.units, self.token = (
            cfg,
            provider,
            transport,
            units,
            token,
        )
        self.sleep, self.log = sleep, log

    def url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.remote.port}/index.html?k={self.token}"

    def _tunnel(self, addr: str) -> None:
        if self.units.active("tunnel") and self.units.target("tunnel") == addr:
            return
        self.units.start("tunnel", self.transport.tunnel_argv(addr), target=addr, restart=True)

    def _loop(self) -> None:
        if not self.units.active("sync"):
            self.units.start(
                "sync",
                [sys.executable, "-m", "agent_workbench", "--root", str(self.cfg.root), "remote", "loop"],
            )

    def up(self, wait_tries: int = 30) -> str:
        if self.provider.status() != "RUNNING":
            self.provider.start()
        addr = None
        for _ in range(wait_tries):
            addr = self.provider.address()
            if addr and self.transport.reachable(addr):
                break
            self.sleep(5)
        else:
            raise RemoteError("the machine did not become reachable over ssh")
        assert addr
        self.transport.push(addr)
        self.transport.serve(addr)
        self._tunnel(addr)
        self._loop()
        return self.url()

    def sync(self) -> str:
        """One round: pull, then stop everything when no card is left, else push and keep the tunnel pointed right."""
        if self.provider.status() != "RUNNING":
            return "not running"
        addr = self.provider.address()
        if not addr:
            return "no address"
        n = self.transport.pull(addr)
        if n:
            self.log(f"{n} remark(s) pulled")
        if self.cfg.remote.auto_stop and sum(open_count(self.cfg, r) for r in list_reports(self.cfg)) == 0:
            self.log("no card is open: stopping")
            self.stop_all()
            return "stopped"
        self.transport.push(addr)
        self._tunnel(addr)
        return "synced"

    def down(self) -> None:
        if self.provider.status() == "RUNNING":
            addr = self.provider.address()
            if addr:
                self.transport.pull(addr)
        self.stop_all()

    def stop_all(self) -> None:
        self.provider.stop()
        self.units.stop("tunnel")
        self.units.stop("sync")  # last: it may be the caller

    def loop(self) -> None:
        while self.sync() not in ("stopped", "not running"):
            self.sleep(self.cfg.remote.sync_seconds)


def load_token(cfg: Config) -> str:
    f = cfg.ws / "remote_token"
    if not f.exists() or not f.read_text().strip():
        f.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            f.write_text(secrets.token_urlsafe(24))
        finally:
            os.umask(old)
    return f.read_text().strip()


def make_remote(cfg: Config) -> Remote:
    token = load_token(cfg)
    return Remote(cfg, make_provider(cfg.remote), SshTransport(cfg, token), Units(cfg), token)
