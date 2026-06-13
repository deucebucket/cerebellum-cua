"""The JSONL-over-stdio REPL (spec Section 5 ``run_stdio_loop``).

On entry it emits a single ``engine_ready`` *event* envelope so a downstream CLI
agent knows the engine is listening, then reads one JSON request per line from
``stdin``, prints ``engine.handle_line(line)`` + newline to ``stdout``, and flushes
after every response. Blank lines are skipped; EOF ends the loop cleanly.
"""

from __future__ import annotations

import sys
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

ENGINE_READY_PAYLOAD = {"version": "4.2", "status": "listening"}


def run_stdio_loop(
    engine: CuaEngine,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> None:
    """Run the blocking JSONL request/response loop until stdin reaches EOF."""
    src = stdin if stdin is not None else sys.stdin
    dst = stdout if stdout is not None else sys.stdout

    ready = engine.protocol.make_envelope(
        "engine_ready", dict(ENGINE_READY_PAYLOAD), type="event"
    )
    _emit(dst, _to_json(engine, ready))

    for line in src:
        if not line.strip():
            continue
        _emit(dst, engine.handle_line(line))


def _to_json(engine: CuaEngine, envelope: dict[str, object]) -> str:
    """Serialize an envelope the same way the protocol serializes responses."""
    import json  # noqa: PLC0415 - local import keeps the module surface tiny

    return json.dumps(envelope, ensure_ascii=False)


def _emit(dst: IO[str], line: str) -> None:
    """Write one framed line and flush so a piped agent sees it immediately."""
    dst.write(line + "\n")
    dst.flush()
