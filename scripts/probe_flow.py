"""In-rig probe: run a candidate flow and report per-step success.

Run via the rig:
    DEMO=/work/scripts/probe_flow.py FLOW=/work/examples/tutorials/gedit_drive.json \
        bash scripts/run-vm.sh

Prints one JSON line per step plus a final ``ALL_OK true/false`` so a wrapper can
gate on a clean run. Uses verify_actions so each action is re-captured/confirmed.
"""
from __future__ import annotations

import json
import os

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.tutorial import Tutorial, run_tutorial


def main() -> None:
    flow = os.environ.get("FLOW", "/work/examples/tutorials/gedit_drive.json")
    tut = Tutorial.from_dict(json.load(open(flow)))
    eng = CuaEngine(db_dsn="/rig/out/probe.db", secret="x",
                    capture_backend_kind="atspi", visible_cursor=True,
                    verify_actions=True)
    try:
        out = run_tutorial(eng, tut)
        for i, e in enumerate(out["timeline"]):
            print(json.dumps({
                "step": i, "caption": e["caption"], "ok": e["ok"],
                "perceived": e.get("perceived", ""), "tokens": e.get("tokens", 0),
                "summary": e.get("result_summary", ""),
            }))
        print(f"ALL_OK {out['success']}")
    finally:
        eng.close()


if __name__ == "__main__":
    main()
