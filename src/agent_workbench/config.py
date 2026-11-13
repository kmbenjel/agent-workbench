"""Configuration: one optional ``workbench.toml``; every key has a default, unknown keys are errors.

The file is looked up from the current directory upwards (or ``--config`` / ``AWB_CONFIG``). Relative paths in it
are relative to the file's directory. Without a file, the current directory is the root and everything is default.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

CONFIG_NAME = "workbench.toml"


class ConfigError(ValueError):
    """A problem in the configuration, worded for the person who has to fix the file."""


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""  # shared secret; also read from AWB_TOKEN. Required when host is not loopback.
    allowed_hosts: list[str] = field(default_factory=list)  # extra Host headers accepted (besides loopback)
    base_remarks: str = ""  # read-only remarks file merged under the live one (remote mode)


@dataclass
class PageConfig:
    lang: str = "en"
    dir: str = "ltr"  # "rtl" for Arabic, Hebrew, Persian...
    title: str = "Review"
    labels: dict[str, str] = field(default_factory=dict)  # override any UI label, see page.LABELS
    image_height: int = 260


@dataclass
class ReviewConfig:
    tick_closes: bool = True  # an approval of the current card revision closes the card
    strict_close: bool = True  # a close note must quote the reviewer, say "my judgment" or start with REOPEN
    settle_seconds: float = (
        45.0  # a remark is reported once unchanged this long (people type as the box saves)
    )
    poll_seconds: float = 2.0


@dataclass
class RemoteConfig:
    provider: str = "ssh"  # ssh (always-on host) | gcloud | command
    host: str = ""  # ssh destination, user@host or an alias from ~/.ssh/config
    ssh_key: str = ""
    remote_dir: str = "agent-workbench-review"
    port: int = 8765
    sync_seconds: float = 20.0
    auto_stop: bool = True  # stop the machine when no card is open any more
    unit_prefix: str = "awb"
    # gcloud provider
    instance: str = ""
    zone: str = ""
    project: str = ""
    # command provider: shell templates; status prints RUNNING when up, address prints the host to ssh to
    start_cmd: str = ""
    stop_cmd: str = ""
    status_cmd: str = ""
    address_cmd: str = ""
    ssh_user: str = ""


@dataclass
class GovernorConfig:
    max_workers: int = 6
    workers_file: str = "workers"  # relative to the workspace; jobs read it before starting each unit of work
    keep_gb: float = 2.5
    per_worker_gb: float = 1.5
    parent_gb: float = 0.5
    pen_slice: str = ""  # a systemd user slice to size (e.g. "jobs.slice"); empty = no pen
    pen_min_gb: float = 2.0
    pen_max_gb: float = 10.0
    pause_mb: int = 400  # a process is eligible for pause/kill from this resident size
    warn_gb: float = 1.5
    warn_pressure: float = 8.0
    kill_gb: float = 0.8
    kill_pressure: float = 20.0
    crit_gb: float = 1.0
    crit_pressure: float = 20.0
    calm_gb: float = 3.0
    calm_pressure: float = 2.0
    resume_after_seconds: float = 20.0
    resume_gap_seconds: float = 30.0
    hold_after_brake_seconds: float = 1200.0
    grow_after_seconds: float = 120.0
    heavy_names: list[str] = field(
        default_factory=lambda: [
            "ffmpeg",
            "python",
            "python3",
            "tesseract",
            "gs",
            "ocrmypdf",
            "magick",
            "convert",
            "pdftoppm",
            "qpdf",
            "mutool",
            "sox",
            "java",
            "rustc",
            "cargo",
            "cc1",
            "cc1plus",
            "ld",
            "go",
            "node",
            "npm",
            "esbuild",
            "aria2c",
            "rclone",
            "xz",
            "zstd",
            "7z",
            "gzip",
            "tar",
            "sort",
        ]
    )
    job_patterns: list[str] = field(default_factory=list)  # regexes on the command line: always eligible
    protect_units: list[str] = field(
        default_factory=lambda: [
            "gnome-",
            "org.gnome",
            "pipewire",
            "wireplumber",
            "dbus",
            "xdg-",
            "agent-workbench-governor",
        ]
    )
    protect_cmd: list[str] = field(
        default_factory=lambda: [r"\.vscode", r"jetbrains", r"agent_workbench.*governor"]
    )
    close_idle_browsers: bool = (
        False  # opt in: SIGTERM Chrome/Brave/Firefox when memory is low and nobody is at the machine
    )
    idle_minutes: float = 10.0


@dataclass
class NetpaceConfig:
    enabled: bool = True
    router: str = ""  # default: the default gateway
    wan: str = "1.1.1.1"
    low_ms: float = 6.0
    high_ms: float = 10.0
    wan_slack_ms: float = 15.0
    max_mb_s: float = 6.0
    min_mb_s: float = 0.4
    calm_minutes: float = 10.0
    calm_lan_ms: float = 8.0
    calm_wan_slack_ms: float = 12.0


@dataclass
class QueueConfig:
    jobs: int = 12  # parallel downloads
    connections: int = 8  # aria2c connections per file
    stage_gb: float = 6.0  # staged (downloaded, not yet delivered) bytes cap
    min_free_gb: float = 8.0  # never fill the disk below this
    net_mb_s: float = 3.0  # total download cap, 0 = none
    stage_dir: str = "stage"  # relative to the workspace
    downloader: str = "auto"  # auto | aria2c | curl | python
    user_agent: str = "agent-workbench/0.1"
    deliver: str = "local"  # local | rclone | command
    deliver_to: str = "downloads"  # local: a directory; rclone: "remote:path"; command: unused
    deliver_cmd: str = ""  # command mode, e.g. "scp {src} host:{dst}" ({src} staged file, {dst} target)
    deliver_lock: str = ""  # a lock file taken around every delivery (one writer at a time)
    deliver_retries: int = 3
    verify_size: bool = True
    verify_magic: str = ""  # e.g. "%PDF"
    verify_tail: str = ""  # e.g. "%%EOF"
    verify_cmd: str = ""  # e.g. "pdfinfo {file}"; non-zero exit = bad file


@dataclass
class ResumableConfig:
    resume_free_gb: float = 4.0
    calm_polls: int = 4
    poll_seconds: float = 30.0
    max_stalls: int = 20
    progress_lines: int = 2


@dataclass
class PowerConfig:
    battery: str = "/sys/class/power_supply/BAT0"
    ac: str = "/sys/class/power_supply/AC"
    limit_pct: int = 7
    poll_seconds: float = 300.0
    near_pct: int = 15
    near_poll_seconds: float = 60.0
    warn_seconds: float = 30.0
    sleep_cmd: str = ""  # e.g. "systemctl hibernate"; empty = only pause the jobs
    held_seconds: float = 300.0
    job_patterns: list[str] = field(default_factory=list)  # regexes on command lines to SIGSTOP


SECTIONS: dict[str, type] = {
    "server": ServerConfig,
    "page": PageConfig,
    "review": ReviewConfig,
    "remote": RemoteConfig,
    "governor": GovernorConfig,
    "netpace": NetpaceConfig,
    "queue": QueueConfig,
    "resumable": ResumableConfig,
    "power": PowerConfig,
}


@dataclass
class Config:
    root: Path = field(default_factory=Path.cwd)
    workspace: str = ".workbench"
    site: str = "site"
    server: ServerConfig = field(default_factory=ServerConfig)
    page: PageConfig = field(default_factory=PageConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    governor: GovernorConfig = field(default_factory=GovernorConfig)
    netpace: NetpaceConfig = field(default_factory=NetpaceConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    resumable: ResumableConfig = field(default_factory=ResumableConfig)
    power: PowerConfig = field(default_factory=PowerConfig)

    # -- paths ---------------------------------------------------------------------------------------------
    @property
    def ws(self) -> Path:
        p = Path(self.workspace)
        return p if p.is_absolute() else self.root / p

    @property
    def site_dir(self) -> Path:
        p = Path(self.site)
        return p if p.is_absolute() else self.root / p

    @property
    def reports_dir(self) -> Path:
        return self.ws / "reports"

    @property
    def remarks_file(self) -> Path:
        return self.ws / "remarks.jsonl"

    @property
    def closed_file(self) -> Path:
        return self.ws / "closed.jsonl"

    @property
    def news_file(self) -> Path:
        return self.ws / "news.jsonl"

    def ensure_dirs(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.site_dir.mkdir(parents=True, exist_ok=True)


def _build(cls: type, data: dict[str, Any], where: str) -> Any:
    names = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(names))
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)}; valid keys: {', '.join(sorted(names))}"
        )
    out = {}
    for k, v in data.items():
        default = getattr(cls(), k)
        if isinstance(default, bool) and not isinstance(v, bool):
            raise ConfigError(f"{where}.{k}: expected true/false, got {v!r}")
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ConfigError(f"{where}.{k}: expected a number, got {v!r}")
            v = type(default)(v)
        if isinstance(default, str) and not isinstance(v, str):
            raise ConfigError(f"{where}.{k}: expected a string, got {v!r}")
        if isinstance(default, list) and not isinstance(v, list):
            raise ConfigError(f"{where}.{k}: expected a list, got {v!r}")
        out[k] = v
    return cls(**out)


def find_config(start: Path | None = None) -> Path | None:
    env = os.environ.get("AWB_CONFIG")
    if env:
        return Path(env)
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_NAME).is_file():
            return d / CONFIG_NAME
    return None


def parse(data: dict[str, Any], root: Path) -> Config:
    top = {k: v for k, v in data.items() if k not in SECTIONS}
    unknown = sorted(set(top) - {"workspace", "site"})
    if unknown:
        raise ConfigError(
            f"unknown top-level key(s) {', '.join(unknown)}; valid: workspace, site, {', '.join(SECTIONS)}"
        )
    cfg = Config(root=root, **{k: str(v) for k, v in top.items()})
    for name, cls in SECTIONS.items():
        if name in data:
            if not isinstance(data[name], dict):
                raise ConfigError(f"[{name}] must be a table")
            setattr(cfg, name, _build(cls, data[name], f"[{name}]"))
    env_token = os.environ.get("AWB_TOKEN")
    if env_token and not cfg.server.token:
        cfg.server.token = env_token
    for env, attr in (("AWB_WORKSPACE", "workspace"), ("AWB_SITE", "site")):
        if os.environ.get(env):
            setattr(cfg, attr, os.environ[env])
    if os.environ.get("AWB_BASE_REMARKS") and not cfg.server.base_remarks:
        cfg.server.base_remarks = os.environ["AWB_BASE_REMARKS"]
    return cfg


def load(path: str | os.PathLike[str] | None = None, root: str | os.PathLike[str] | None = None) -> Config:
    """Load the configuration; ``root`` (or ``AWB_ROOT``) overrides the directory that relative paths use."""
    p = Path(path) if path else find_config()
    env_root = os.environ.get("AWB_ROOT")
    base = Path(root) if root else (Path(env_root) if env_root else (p.parent if p else Path.cwd()))
    if p is None:
        return parse({}, base.resolve())
    if tomllib is None:
        raise ConfigError("reading workbench.toml needs Python 3.11+ or `pip install tomli`")
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read {p}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p} is not valid TOML: {e}") from e
    return parse(data, base.resolve())
