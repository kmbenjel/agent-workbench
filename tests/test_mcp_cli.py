import io
import json
import subprocess
import sys

import pytest

from agent_workbench import cli, mcp_server, store
from agent_workbench.config import ConfigError, load, parse


def rpc(cfg, *msgs):
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in msgs))
    stdout = io.StringIO()
    mcp_server.serve_stdio(cfg, stdin, stdout)
    return [json.loads(x) for x in stdout.getvalue().splitlines()]


def call(cfg, name, **args):
    r = rpc(
        cfg, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    )[0]["result"]
    return r["isError"], json.loads(r["content"][0]["text"]) if not r["isError"] else r["content"][0]["text"]


def test_initialize_and_tools_list(cfg):
    out = rpc(
        cfg,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    )
    assert len(out) == 3  # the notification gets no answer
    assert (
        out[0]["result"]["protocolVersion"] == "2025-03-26"
        and out[0]["result"]["serverInfo"]["name"] == "agent-workbench"
    )
    assert {t["name"] for t in out[1]["result"]["tools"]} == {
        "post_news",
        "read_remarks",
        "make_review_page",
        "close_card",
    }
    assert out[2]["result"] == {}


def test_unknown_protocol_version_gets_a_supported_one(cfg):
    out = rpc(cfg, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999"}})
    assert out[0]["result"]["protocolVersion"] in mcp_server.SUPPORTED


def test_errors(cfg):
    out = rpc(
        cfg,
        {"jsonrpc": "2.0", "id": 1, "method": "nope"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "rm_rf"}},
    )
    assert out[0]["error"]["code"] == -32601 and out[1]["error"]["code"] == -32602
    stdout = io.StringIO()
    mcp_server.serve_stdio(cfg, io.StringIO("garbage\n[1]\n\n"), stdout)
    assert stdout.getvalue().count("parse error") == 2


def test_full_loop_through_mcp(cfg):
    err, res = call(
        cfg,
        "make_review_page",
        report="job",
        title="T",
        cards=[{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
    )
    assert not err and res["open_cards"] == 2 and res["page"].endswith("job.html")
    err, res = call(cfg, "post_news", text="two cards for you", kind="question")
    assert not err and res["kind"] == "question"
    store.append_jsonl(cfg.remarks_file, store.make_remark("job", "a", "reject", "use the older scan"))
    err, res = call(cfg, "read_remarks")
    assert [r["card"] for r in res] == ["a"] and res[0]["comment"] == "use the older scan"
    err, text = call(cfg, "close_card", report="job", card="a", note="fixed")
    assert err and "must quote" in text
    err, text = call(cfg, "close_card", report="job", card="a", note="swapped: «use the older scan»")
    assert not err
    assert call(cfg, "read_remarks")[1] == []
    assert call(cfg, "read_remarks", unhandled=False)[1][0]["card"] == "a"


def test_tool_errors_are_results_not_crashes(cfg):
    err, text = call(cfg, "make_review_page", report="x", cards=[{"title": "no id"}])
    assert err and "no id" in text
    err, text = call(cfg, "post_news", text="x", kind="shout")
    assert err
    err, text = call(cfg, "post_news")  # missing required argument
    assert err and "missing argument 'text'" in text
    # wrong types are answers too: the server must outlive any input a model can produce
    err, text = call(cfg, "make_review_page", report=7, cards=[{"id": "a"}])
    assert err
    err, text = call(cfg, "make_review_page", report="x", cards="not a list")
    assert err and "cards" in text
    out = rpc(
        cfg,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "post_news", "arguments": [1]},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": "nonsense"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    )
    assert out[0]["error"]["code"] == -32602 and out[1]["error"]["code"] == -32602 and out[2]["result"] == {}


def test_read_remarks_is_marked_read_only():
    by_name = {t["name"]: t for t in mcp_server.TOOLS}
    assert by_name["read_remarks"]["annotations"] == {"readOnlyHint": True}
    assert all("annotations" not in t for n, t in by_name.items() if n != "read_remarks")


def test_mcp_over_a_real_pipe(tmp_path):
    p = subprocess.run(
        [sys.executable, "-m", "agent_workbench", "mcp"],
        input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
    )
    assert json.loads(p.stdout)["result"]["tools"][0]["name"] == "post_news"


# -- CLI ------------------------------------------------------------------------------------------------------------
def run(tmp_path, *args):
    return cli.main(["--root", str(tmp_path), *args])


def test_cli_loop(tmp_path, capsys):
    cards = tmp_path / "c.jsonl"
    cards.write_text('{"id":"1","title":"one"}\n{"id":"2","title":"two"}\n')
    assert run(tmp_path, "page", "build", "r", "--cards", str(cards), "--title", "My") == 0
    assert "2 cards open" in capsys.readouterr().out
    assert run(tmp_path, "page", "list") == 0 and "r\t2 open" in capsys.readouterr().out
    assert run(tmp_path, "news", "post", "hello", "there", "--kind", "question") == 0
    capsys.readouterr()
    cfg = parse({}, tmp_path)
    store.append_jsonl(cfg.remarks_file, store.make_remark("r", "1", "reject", "wrong édition"))
    store.append_jsonl(
        cfg.remarks_file, store.make_remark("_news", store.read_news(cfg.news_file)[0]["id"], "", "British")
    )
    assert run(tmp_path, "remarks", "--json") == 0
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert {r["card"] for r in rows} >= {"1"}
    assert run(tmp_path, "news", "list") == 0 and "reply: British" in capsys.readouterr().out
    assert run(tmp_path, "close", "r", "1", "no quote") == 1
    assert "must quote" in capsys.readouterr().err
    assert run(tmp_path, "close", "r", "1", "swapped:", "«wrong", "édition»") == 0
    assert run(tmp_path, "status", "--json") == 0
    s = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert s["reports"] == {"r": 1} and s["news"] == 1


def test_cli_watch_once(tmp_path, capsys):
    cfg = parse({}, tmp_path)
    store.append_jsonl(cfg.remarks_file, store.make_remark("r", "1", "approve", "yes"))
    assert run(tmp_path, "watch", "--once", "--from-start") == 0
    assert "REMARK r 1 verdict=approve «yes»" in capsys.readouterr().out
    assert run(tmp_path, "watch", "--once", "--from-start") == 0
    assert capsys.readouterr().out == ""  # the saved position: nothing repeated


def test_cli_errors_are_worded_not_tracebacks(tmp_path, capsys):
    assert run(tmp_path, "page", "build", "missing") == 2
    assert "no saved report" in capsys.readouterr().err
    (tmp_path / "workbench.toml").write_text("[server]\nprot = 1\n")
    assert cli.main(["--config", str(tmp_path / "workbench.toml"), "status"]) == 2
    assert "unknown key(s) prot" in capsys.readouterr().err
    assert run(tmp_path, "queue", "run", str(tmp_path / "nope.tsv")) == 2


def test_cli_init_and_demo(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert cli.main(["--root", str(tmp_path), "init"]) == 1
    cfg = load(tmp_path / "workbench.toml")  # the sample config is valid
    assert cfg.server.port == 8765
    d = tmp_path / "demo"
    assert cli.main(["demo", str(d)]) == 0
    html = (d / "site/ocr-fixes.html").read_text()
    assert html.count("<article") == 7 and "universaily" in html
    assert (d / ".workbench/news.jsonl").exists()


def test_help_and_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert cli.main([]) == 0 and "Try it:  awb demo" in capsys.readouterr().out


def test_config_validation(tmp_path):
    with pytest.raises(ConfigError, match="unknown top-level"):
        parse({"serverr": {}}, tmp_path)
    with pytest.raises(ConfigError, match="expected a number"):
        parse({"server": {"port": "80"}}, tmp_path)
    with pytest.raises(ConfigError, match="true/false"):
        parse({"review": {"tick_closes": "yes"}}, tmp_path)
    with pytest.raises(ConfigError, match="a table"):
        parse({"server": 3}, tmp_path)
    (tmp_path / "bad.toml").write_text("= nonsense")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(tmp_path / "bad.toml")
    c = parse({"queue": {"stage_gb": 2}}, tmp_path)
    assert c.queue.stage_gb == 2.0 and isinstance(c.queue.stage_gb, float)


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("AWB_TOKEN", "t")
    monkeypatch.setenv("AWB_WORKSPACE", "ws2")
    c = parse({}, tmp_path)
    assert c.server.token == "t" and c.ws == tmp_path / "ws2"


def test_example_config_matches_the_packaged_one():
    from importlib import resources
    from pathlib import Path

    packaged = resources.files("agent_workbench").joinpath("templates", "workbench.toml").read_text()
    example = Path(__file__).parent.parent / "examples" / "workbench.toml"
    if example.exists():
        assert example.read_text() == packaged
