"""Unit tests for tutorial runner token/perceived/total instrumentation."""

from __future__ import annotations

from typing import Any

from cerebellum_cua.tutorial import Tutorial, run_tutorial


class _FakeEngine:
    def __init__(self) -> None:
        self.handlers = {
            "run_skill": lambda p: {"success": True, "action": "click",
                                    "affected_rows": [1], "name": "Open"},
            "read_text": lambda p: {"texts": [{"row_id": 1, "text": "Open",
                                               "bbox": [0, 0, 40, 20]}], "count": 1},
        }


def _clock() -> Any:
    t = {"n": 0.0}

    def tick() -> float:
        t["n"] += 1.0
        return t["n"]

    return tick


def test_runner_records_tokens_and_totals() -> None:
    tut = Tutorial.from_dict({"title": "t", "steps": [
        {"caption": "click open", "action": "skill", "name": "click",
         "args": {"target": "Open"}, "hold": 1.0},
        {"caption": "read", "action": "op", "name": "read_text",
         "args": {}, "hold": 1.0},
    ]})
    out = run_tutorial(_FakeEngine(), tut, clock=_clock())
    tl = out["timeline"]
    assert tl[0]["tokens"] > 0           # real estimate of the skill result
    assert tl[1]["tokens"] > 0
    assert out["totals"]["a11y_tokens"] == tl[0]["tokens"] + tl[1]["tokens"]


def test_runner_records_perceived_from_result_name() -> None:
    tut = Tutorial.from_dict({"title": "t", "steps": [
        {"caption": "click open", "action": "skill", "name": "click",
         "args": {"target": "Open"}, "hold": 1.0},
    ]})
    out = run_tutorial(_FakeEngine(), tut, clock=_clock())
    assert out["timeline"][0]["perceived"] == "Open"
