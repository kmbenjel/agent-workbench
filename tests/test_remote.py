import subprocess
from pathlib import Path

import pytest

from agent_workbench import page, remote
from agent_workbench.config import RemoteConfig, parse


def cp(out="", rc=0):
    return subprocess.CompletedProcess([], rc, out, "")


class FakeProvider:
    def __init__(self, status="RUNNING"):
        self._status, self.started, self.stopped = status, 0, 0

    def status(self):
        return self._status

    def start(self):
        self._status = "RUNNING"
        self.started += 1

    def stop(self):
        self._status = "STOPPED"
        self.stopped += 1

    def address(self):
        return "10.0.0.9"


class FakeTransport:
    def __init__(self, pulled=0, reachable_after=0):
        self.calls, self.pulled, self.n = [], pulled, reachable_after

    def reachable(self, addr):
        self.calls.append("reachable")
        self.n -= 1
        return self.n < 0

    def push(self, addr):
        self.calls.append("push")

    def pull(self, addr):
        self.calls.append("pull")
        return self.pulled

    def serve(self, addr):
        self.calls.append("serve")

    def tunnel_argv(self, addr):
        return ["ssh", "-N", addr]


class FakeUnits:
    def __init__(self):
        self.running, self.targets, self.log = set(), {}, []

    def active(self, n):
        return n in self.running

    def target(self, n):
        return self.targets.get(n, "")

    def start(self, n, argv, target="", restart=False):
        self.running.add(n)
        self.targets[n] = target
        self.log.append(("start", n))

    def stop(self, n):
        self.running.discard(n)
        self.log.append(("stop", n))


def make(tmp_path, cards=1, provider=None, transport=None, **rc):
    cfg = parse({"remote": {"host": "h", **rc}}, tmp_path)
    page.save_report(cfg, "r", {"cards": [{"id": str(i)} for i in range(cards)] or []})
    units, msgs = FakeUnits(), []
    r = remote.Remote(
        cfg,
        provider or FakeProvider(),
        transport or FakeTransport(),
        units,
        "TOK",
        sleep=lambda s: None,
        log=msgs.append,
    )
    return r, units, msgs, cfg


def test_merge_pulled_appends_only_new_lines(tmp_path):
    local, pulled = tmp_path / "l.jsonl", tmp_path / "p.jsonl"
    local.write_text('{"a":1}\n{"a":2}\n')
    pulled.write_text('{"a":2}\n\n{"a":3}\n{"a":4}\n')
    assert remote.merge_pulled(local, pulled) == 2
    assert local.read_text().splitlines() == ['{"a":1}', '{"a":2}', '{"a":3}', '{"a":4}']
    assert remote.merge_pulled(local, pulled) == 0
    assert remote.merge_pulled(tmp_path / "new.jsonl", pulled) == 3


def test_sync_pulls_pushes_and_keeps_tunnel(tmp_path):
    r, units, msgs, _ = make(tmp_path, transport=FakeTransport(pulled=2))
    assert r.sync() == "synced"
    assert r.transport.calls == ["pull", "push"]
    assert ("start", "tunnel") in units.log and "2 remark(s) pulled" in msgs
    r.sync()  # same address: the tunnel is not restarted
    assert units.log.count(("start", "tunnel")) == 1


def test_sync_stops_the_machine_when_no_card_is_open(tmp_path):
    r, units, msgs, _ = make(tmp_path, cards=0)
    assert r.sync() == "stopped"
    assert r.provider.stopped == 1 and "tunnel" not in units.running
    assert units.log[-1] == ("stop", "sync")  # the sync unit last: it may be the caller
    assert r.transport.calls == ["pull"]  # a last pull, no push


def test_auto_stop_can_be_off(tmp_path):
    r, *_ = make(tmp_path, cards=0, auto_stop=False)
    assert r.sync() == "synced" and r.provider.stopped == 0


def test_sync_is_a_noop_when_machine_is_off(tmp_path):
    r, *_ = make(tmp_path, provider=FakeProvider("STOPPED"))
    assert r.sync() == "not running" and r.transport.calls == []


def test_open_cards_counts_ticks_so_a_finished_review_stops_the_machine(tmp_path):
    from agent_workbench import store

    r, _, _, cfg = make(tmp_path, cards=2)
    for c in ("0", "1"):
        store.append_jsonl(
            cfg.remarks_file, store.make_remark("r", c, "approve", "", store.card_rev({"id": c}))
        )
    assert r.sync() == "stopped"


def test_up_starts_waits_pushes_serves_and_returns_the_url(tmp_path):
    r, units, _, _ = make(
        tmp_path, provider=FakeProvider("STOPPED"), transport=FakeTransport(reachable_after=2)
    )
    url = r.up()
    assert url == "http://127.0.0.1:8765/index.html?k=TOK"
    assert r.provider.started == 1
    assert r.transport.calls == ["reachable"] * 3 + ["push", "serve"]
    assert {"tunnel", "sync"} <= units.running


def test_up_gives_up_when_unreachable(tmp_path):
    r, *_ = make(tmp_path, transport=FakeTransport(reachable_after=999))
    with pytest.raises(remote.RemoteError, match="reachable"):
        r.up(wait_tries=3)


def test_down_takes_a_last_pull_then_stops_everything(tmp_path):
    r, units, _, _ = make(tmp_path)
    r.down()
    assert r.transport.calls == ["pull"] and r.provider.stopped == 1


def test_loop_runs_until_stopped(tmp_path):
    r, *_ = make(tmp_path, cards=0)
    r.loop()
    assert r.provider.stopped == 1


def test_gcloud_provider_builds_commands():
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return cp("RUNNING" if "describe" in argv else "")

    p = remote.GcloudProvider(RemoteConfig(instance="vm1", zone="z-b", project="proj"), run)
    assert p.status() == "RUNNING"
    p.start()
    p.stop()
    assert seen[0][:5] == ["gcloud", "compute", "instances", "--project", "proj"]
    assert seen[0][5:8] == ["describe", "vm1", "--zone"]
    assert [a[5] for a in seen] == ["describe", "start", "stop"]
    with pytest.raises(remote.RemoteError):
        remote.GcloudProvider(RemoteConfig())


def test_command_provider_and_factory():
    calls = []

    def run(cmd, shell=False, **kw):
        calls.append((cmd, shell))
        return cp({"st": "RUNNING\n", "ad": "1.2.3.4\n"}.get(cmd, ""), 1 if cmd == "bad" else 0)

    p = remote.CommandProvider(
        RemoteConfig(status_cmd="st", address_cmd="ad", start_cmd="go", stop_cmd="halt"), run
    )
    assert p.status() == "RUNNING" and p.address() == "1.2.3.4"
    p.start()
    p.stop()
    assert ("go", True) in calls and ("halt", True) in calls
    with pytest.raises(remote.RemoteError, match="start_cmd failed"):
        remote.CommandProvider(RemoteConfig(status_cmd="st", address_cmd="ad", start_cmd="bad"), run).start()
    with pytest.raises(remote.RemoteError):
        remote.make_provider(RemoteConfig(provider="nope"))
    assert isinstance(remote.make_provider(RemoteConfig(host="h")), remote.StaticProvider)
    with pytest.raises(remote.RemoteError, match="host is empty"):
        remote.make_provider(RemoteConfig())


def test_ssh_transport_commands(tmp_path, monkeypatch):
    cfg = parse(
        {"remote": {"host": "h", "ssh_user": "bob", "ssh_key": "~/k", "port": 9000, "remote_dir": "rd"}},
        tmp_path,
    )
    cfg.remarks_file.parent.mkdir(parents=True)
    seen = []

    def run(argv, input=None, **kw):
        seen.append((argv, input))
        return cp("serving\n" if "serving" in " ".join(argv) else "")

    monkeypatch.setattr(remote.shutil, "which", lambda x: "/usr/bin/" + x)
    t = remote.SshTransport(cfg, "SECRET", run)
    t.push("1.2.3.4")
    rs = [a for a, _ in seen if a[0] == "rsync"]
    assert rs[0][-1] == "bob@1.2.3.4:rd/pkg/agent_workbench/" and "__pycache__" in rs[0]
    assert any(a[-1].endswith("rd/site/") for a in rs)
    t.serve("1.2.3.4")
    env_call = [(a, i) for a, i in seen if "cat >" in a[-1]][0]
    assert env_call[1] == "AWB_TOKEN=SECRET\n"
    assert not any("SECRET" in " ".join(a) for a, _ in seen)  # the token never shows on a command line
    serve_cmd = [a[-1] for a, _ in seen if "serve --host 127.0.0.1" in a[-1]][0]
    assert "--port 9000" in serve_cmd and "[a]gent_workbench" in serve_cmd and "0.0.0.0" not in serve_cmd
    tun = t.tunnel_argv("1.2.3.4")
    assert "127.0.0.1:9000:127.0.0.1:9000" in tun and tun[-1] == "bob@1.2.3.4" and "BatchMode=yes" in tun
    assert str(Path("~/k").expanduser()) in tun


def test_serve_failure_is_reported(tmp_path, monkeypatch):
    cfg = parse({"remote": {"host": "h"}}, tmp_path)
    t = remote.SshTransport(cfg, "T", lambda *a, **k: cp("", 1))
    with pytest.raises(remote.RemoteError, match="did not start"):
        t.serve("x")


def test_pull_merges_and_tolerates_missing_remote_file(tmp_path, monkeypatch):
    cfg = parse({"remote": {"host": "h"}}, tmp_path)
    monkeypatch.setattr(remote.shutil, "which", lambda x: "/usr/bin/" + x)
    tmp = cfg.ws / "remote-pull.tmp"

    def run_ok(argv, **kw):
        tmp.write_text('{"card":"a","report":"r","time":"t"}\n')
        return cp()

    assert remote.SshTransport(cfg, "T", run_ok).pull("x") == 1
    assert not tmp.exists()
    assert remote.SshTransport(cfg, "T", lambda *a, **k: cp("", 23)).pull("x") == 0


def test_token_is_created_once_and_private(tmp_path):
    cfg = parse({}, tmp_path)
    t = remote.load_token(cfg)
    assert len(t) >= 24 and remote.load_token(cfg) == t
    assert (cfg.ws / "remote_token").stat().st_mode & 0o077 == 0


def test_units_process_mode(tmp_path, monkeypatch):
    cfg = parse({}, tmp_path)
    monkeypatch.setattr(remote.shutil, "which", lambda x: None)
    u = remote.Units(cfg)
    assert not u.systemd
    u.start("t", ["sleep", "30"], target="addr")
    try:
        assert u.active("t") and u.target("t") == "addr"
    finally:
        u.stop("t")
    import time

    time.sleep(0.2)
    assert not u.active("t")
