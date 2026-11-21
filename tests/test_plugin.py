"""The Claude Code plugin as a client would load it: manifests, skills, the awb shim, the MCP server, the monitor.

``claude plugin validate .`` is the authoritative check of the manifests; these tests keep the same facts true in
CI, where the Claude Code CLI is not installed, and start the things the plugin starts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent_workbench

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
MARKET = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
posix = pytest.mark.skipif(sys.platform == "win32", reason="the shim is a POSIX shell script")


def front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no front matter"
    head = text[4:].split("\n---\n", 1)[0]
    return dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)


def substitute(value: str) -> str:
    return value.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))


def test_manifests_agree_on_name_and_version():
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", PLUGIN["name"])
    entry = MARKET["plugins"][0]
    assert entry["name"] == PLUGIN["name"] and entry["source"] == "./"
    assert MARKET["name"] and MARKET["owner"]["name"]
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{agent_workbench.__version__}"' in pyproject
    assert PLUGIN["version"] == entry["version"] == agent_workbench.__version__
    assert (ROOT / PLUGIN["icon"]).is_file()


def test_every_skill_and_command_says_when_to_use_it():
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert {p.parent.name for p in skills} == {"review-loop", "remote-review", "job-hygiene"}
    for p in skills:
        fm = front_matter(p)
        assert fm["name"] == p.parent.name, p
        assert 80 <= len(fm["description"]) <= 1024 and " Use " in fm["description"], p  # what + when
    commands = sorted((ROOT / "commands").glob("*.md"))
    assert {p.stem for p in commands} == {"review", "watch", "news", "status"}
    for p in commands:
        assert front_matter(p)["description"], p


def test_docs_name_the_real_install_commands():
    """`/plugin install NAME@MARKETPLACE` after `/plugin marketplace add OWNER/REPO`: both names come from the
    manifests, so a rename cannot leave the README behind."""
    add = "/plugin marketplace add kmbenjel/agent-workbench"
    install = f"/plugin install {PLUGIN['name']}@{MARKET['name']}"
    for doc in ("README.md", "docs/ar/install.md", "docs/ar/guides/claude-code.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert add in text and install in text, doc
        assert "--marketplace" not in text, doc


@posix
def test_the_shim_runs_the_bundled_package_from_any_directory(tmp_path):
    shim = ROOT / "bin" / "awb"
    assert os.access(shim, os.X_OK)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
    p = subprocess.run([str(shim), "--version"], capture_output=True, text=True, cwd=tmp_path, env=env)
    assert p.returncode == 0 and p.stdout.strip() == f"agent-workbench {agent_workbench.__version__}"
    # through a symlinked directory too (plugin caches may link)
    (tmp_path / "plug").symlink_to(ROOT, target_is_directory=True)
    p = subprocess.run(
        [str(tmp_path / "plug" / "bin" / "awb"), "status", "--json"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )
    assert p.returncode == 0 and json.loads(p.stdout)["open"] == 0


def test_the_mcp_server_starts_as_the_plugin_declares_it(tmp_path):
    """Run exactly the command of .mcp.json, as Claude Code would, and do the opening handshake."""
    conf = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["agent-workbench"]
    argv = [substitute(conf["command"]), *map(substitute, conf["args"])]
    if argv[0] == "python3":
        argv[0] = sys.executable
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({k: substitute(v) for k, v in conf.get("env", {}).items()})
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "make_review_page",
                "arguments": {"report": "r", "cards": [{"id": "a", "title": "A"}]},
            },
        },
    ]
    p = subprocess.run(
        argv,
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    out = [json.loads(x) for x in p.stdout.splitlines()]
    assert [o["id"] for o in out] == [1, 2, 3] and p.returncode == 0
    assert out[0]["result"]["protocolVersion"] == "2025-11-25"
    assert out[0]["result"]["capabilities"] == {"tools": {}}
    for tool in out[1]["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object" and tool["description"]
    assert out[2]["result"]["isError"] is False and (tmp_path / "site" / "r.html").is_file()


def test_the_monitor_is_the_watcher_started_by_the_review_skill():
    monitors = json.loads((ROOT / "monitors" / "monitors.json").read_text())
    assert [set(m) <= {"name", "command", "description", "when"} for m in monitors] == [
        True
    ]  # strict objects
    m = monitors[0]
    assert m["when"] == "on-skill-invoke:review-loop" and (ROOT / "skills" / "review-loop").is_dir()
    assert m["command"] == '"${CLAUDE_PLUGIN_ROOT}"/bin/awb watch'  # quoted: one word whatever the path holds
    assert "user_config" not in m["command"]


@posix
def test_the_monitor_command_streams_a_remark(tmp_path):
    import select

    from agent_workbench import store

    ws = tmp_path / ".workbench"
    ws.mkdir()
    (tmp_path / "workbench.toml").write_text("[review]\nsettle_seconds = 0\npoll_seconds = 0.1\n")
    command = substitute(json.loads((ROOT / "monitors" / "monitors.json").read_text())[0]["command"])
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
    p = subprocess.Popen(command, shell=True, cwd=tmp_path, stdout=subprocess.PIPE, env=env)
    try:
        for _ in range(100):  # the watcher starts at the end of the file: write once it holds the stream
            if (ws / "watch.lock").exists() and (ws / "watch.lock").read_text():
                break
            select.select([], [], [], 0.1)
        store.append_jsonl(ws / "remarks.jsonl", store.make_remark("r", "a", "reject", "keep it"))
        ready, _, _ = select.select([p.stdout], [], [], 20)
        assert ready and p.stdout.readline().decode().strip() == "REMARK r a verdict=reject «keep it»"
    finally:
        p.kill()
        p.wait()
