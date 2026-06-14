"""JSONL v4.2 message framing and dispatch (spec Section 4).

The :class:`Protocol` is transport-agnostic glue: it parses one line of UTF-8
line-delimited JSON, looks up the matching operation handler, invokes it with the
request payload, and frames the result in a response envelope. Engine logic is
*injected* — the protocol owns framing/dispatch only and never imports the uia,
matrix, or storage internals or walks any live tree.

Envelope shape (spec ``message_envelope``)::

    {"msg_id", "timestamp", "type", "operation", "payload", "error"}

On a raised :class:`~cerebellum_cua.errors.MatrixUIError` the handler's failure is
serialized via ``err.to_dict()`` into the envelope ``error`` slot; an unknown
operation yields code ``9999`` ``UNKNOWN_OPERATION``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from cerebellum_cua.errors import MatrixUIError

#: The operations the engine dispatches. The first five are the v4.2 core
#: contract; ``screenshot`` is an opt-in on-demand visual-capture extension,
#: ``read_text`` aggregates on-screen text + coords from a stored snapshot, and
#: ``run_skill`` runs a named high-level skill (resolve + act + optional verify).
OPERATIONS = (
    "build_matrix",
    "get_element",
    "load_children",
    "invoke_action",
    "get_snapshot_diff",
    "screenshot",
    "read_text",
    "run_skill",
)

UNKNOWN_OPERATION_CODE = 9999
UNKNOWN_OPERATION_MESSAGE = "UNKNOWN_OPERATION"

#: An operation handler takes the request payload dict and returns a payload dict.
Handler = Callable[[dict[str, Any]], dict[str, Any]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Protocol:
    """Frame and dispatch a single JSONL request/response cycle."""

    def make_envelope(
        self,
        operation: str | None,
        payload: dict[str, Any] | None,
        msg_id: str | None = None,
        type: str = "response",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a v4.2 message envelope with a generated msg_id/timestamp."""
        return {
            "msg_id": msg_id or str(uuid.uuid4()),
            "timestamp": _utc_now_iso(),
            "type": type,
            "operation": operation,
            "payload": payload,
            "error": error,
        }

    def handle_line(
        self, raw_line: str, handlers: dict[str, Handler]
    ) -> str:
        """Parse one request line, dispatch it, return one response line.

        ``handlers`` maps operation name -> callable; the engine/cli registers
        ``build_matrix``/``get_element``/``load_children``/``invoke_action``/
        ``get_snapshot_diff``. Returns a JSON string (no trailing newline) — the
        caller is responsible for newline-framing on the wire.
        """
        msg_id: str | None = None
        operation: str | None = None
        try:
            request = json.loads(raw_line.strip())
        except (json.JSONDecodeError, ValueError) as exc:
            return self._error_line(
                msg_id, operation,
                code=UNKNOWN_OPERATION_CODE,
                message="MALFORMED_REQUEST",
                details={"error": str(exc)},
            )

        msg_id = request.get("msg_id")
        operation = request.get("operation")
        payload = request.get("payload") or {}

        handler = handlers.get(operation) if operation else None
        if handler is None:
            return self._error_line(
                msg_id, operation,
                code=UNKNOWN_OPERATION_CODE,
                message=UNKNOWN_OPERATION_MESSAGE,
                details={"operation": operation},
            )

        try:
            result = handler(payload)
        except MatrixUIError as exc:
            return self._error_line(
                msg_id, operation, **exc.to_dict()
            )
        envelope = self.make_envelope(operation, result, msg_id=msg_id)
        return json.dumps(envelope, ensure_ascii=False)

    # --- internals -------------------------------------------------------
    def _error_line(
        self,
        msg_id: str | None,
        operation: str | None,
        *,
        code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> str:
        envelope = self.make_envelope(
            operation,
            payload=None,
            msg_id=msg_id,
            error={"code": code, "message": message, "details": details or {}},
        )
        return json.dumps(envelope, ensure_ascii=False)
