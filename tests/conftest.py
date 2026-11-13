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
