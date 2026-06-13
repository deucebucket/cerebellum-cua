"""CLI / engine layer: the composition root + JSONL stdio REPL.

This is the top of the dependency stack. It wires storage + capture + matrix +
gateway + semantics into a single :class:`CuaEngine` and exposes the
JSONL-over-stdio REPL (:func:`run_stdio_loop`) and the console-script entry point
(:func:`main`). Live capture goes through the OS-neutral :mod:`cerebellum_cua.capture`
seam (UIA on Windows, AT-SPI on Linux), so this package imports cleanly anywhere.
"""

from __future__ import annotations

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.cli.repl import run_stdio_loop

__all__ = ["CuaEngine", "run_stdio_loop", "main"]


def main(argv: object = None) -> int:
    """Lazy proxy to :func:`cerebellum_cua.cli.__main__.main` (avoids argparse import cost)."""
    from cerebellum_cua.cli.__main__ import main as _main  # noqa: PLC0415

    return _main(argv)  # type: ignore[arg-type]
