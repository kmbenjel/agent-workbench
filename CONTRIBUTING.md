# Contributing

Thanks for helping. The project is small on purpose: standard library only at runtime, files instead of services.

## Set up

```bash
git clone https://github.com/kmbenjel/agent-workbench && cd agent-workbench
make install        # a venv in .venv with the dev tools (pytest, coverage, ruff)
make test           # about 10 seconds
make lint
```

Python 3.10 to 3.13 are supported (`tomli` is the only dependency, and only on 3.10).

## Ground rules

- **No runtime dependencies** unless the feature cannot exist without one; discuss it in an issue first.
- **Everything with a decision in it gets a test.** The governor, the watcher, the queue and the remote controller
  take their inputs (processes, clock, commands) as arguments so tests use fakes: follow that pattern, do not mock
  `subprocess` globally.
- Keep the JSONL protocol backward compatible (`docs/protocol.md`); add fields, do not change meanings; bump `v`
  only for a breaking change.
- Heavy work (benchmarks, media rendering, competitor installs) is run on a separate machine, not a laptop in use.
- Security-relevant changes (server, remote, queue targets, the comment backend) need a test that shows the
  refused case. What only a browser can show (the page's policy, a cross-origin save) goes in
  `tests/test_browser.py` (`make test-browser`, headless Chrome).

## Pull requests

One topic per PR, with tests and a line in `CHANGELOG.md` under *Unreleased*. `make lint test` must pass. By
contributing you agree that your work is released under the MIT licence.
