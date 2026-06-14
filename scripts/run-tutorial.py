#!/usr/bin/env python3
"""Load a tutorial JSON, run it against a live engine, print its timeline.

This is the driver the recording harness invokes inside the rig: it builds a
:class:`~cerebellum_cua.cli.engine.CuaEngine` configured for the on-screen demo
session (AT-SPI capture, visible cursor), runs the authored tutorial via
:func:`~cerebellum_cua.tutorial.run_tutorial`, and prints the resulting timeline
as JSON to stdout. The timeline is what
:func:`~cerebellum_cua.tutorial.burn_captions` later overlays onto the recording.

Run::

    PYTHONPATH=src python3 scripts/run-tutorial.py examples/tutorials/gedit_basics.json
    PYTHONPATH=src python3 scripts/run-tutorial.py TUTORIAL.json --db-dsn :memory: --secret demo
"""

from __future__ import annotations

import argparse
import json
import sys

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.tutorial import Tutorial, run_tutorial


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the driver's command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tutorial", help="path to the tutorial JSON document")
    parser.add_argument(
        "--db-dsn",
        default=None,
        help="storage DSN (default: in-memory SQLite for the demo session)",
    )
    parser.add_argument(
        "--secret",
        default="tutorial-demo-secret",
        help="lazy-token codec secret (demo-only, not security-relevant)",
    )
    parser.add_argument(
        "--capture-backend",
        default="atspi",
        help="capture backend kind (default: atspi for the Linux rig)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Load + run the tutorial and emit its timeline JSON. Returns an exit code."""
    args = _parse_args(argv)
    with open(args.tutorial, encoding="utf-8") as fh:
        tutorial = Tutorial.from_dict(json.load(fh))

    with CuaEngine(
        db_dsn=args.db_dsn,
        secret=args.secret,
        capture_backend_kind=args.capture_backend,
        visible_cursor=True,
    ) as engine:
        result = run_tutorial(engine, tutorial)

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
