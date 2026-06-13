"""Unit tests for the JSONL protocol framing/dispatch (gateway/protocol.py).

Covers a happy-path dispatch to an injected handler, MatrixUIError -> error
envelope serialization, unknown operation (9999), and malformed input.
"""

from __future__ import annotations

import json

from cerebellum_cua.errors import ElementNotFoundError
from cerebellum_cua.gateway.protocol import (
    UNKNOWN_OPERATION_CODE,
    Protocol,
)


def _line(operation, payload, msg_id="m-1"):
    return json.dumps(
        {"msg_id": msg_id, "type": "request",
         "operation": operation, "payload": payload}
    )


def test_make_envelope_populates_defaults():
    proto = Protocol()
    env = proto.make_envelope("get_element", {"element": {}})
    assert env["type"] == "response"
    assert env["operation"] == "get_element"
    assert env["payload"] == {"element": {}}
    assert env["error"] is None
    assert env["msg_id"]
    assert env["timestamp"]


def test_handle_line_happy_path_dispatches_to_handler():
    proto = Protocol()
    captured = {}

    def get_element(payload):
        captured.update(payload)
        return {"element": {"row_id": payload["row_id"], "name": "File"}}

    out = proto.handle_line(
        _line("get_element", {"snapshot_id": 47, "row_id": 17}),
        {"get_element": get_element},
    )
    resp = json.loads(out)
    assert resp["msg_id"] == "m-1"
    assert resp["type"] == "response"
    assert resp["operation"] == "get_element"
    assert resp["payload"]["element"]["row_id"] == 17
    assert resp["error"] is None
    assert captured["snapshot_id"] == 47


def test_handle_line_serializes_cerebellum_cua_error():
    proto = Protocol()

    def get_element(payload):
        raise ElementNotFoundError(snapshot_id=47, row_id=999)

    out = proto.handle_line(
        _line("get_element", {"row_id": 999}),
        {"get_element": get_element},
    )
    resp = json.loads(out)
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1002
    assert resp["error"]["message"] == "ELEMENT_NOT_FOUND"
    assert resp["error"]["details"]["row_id"] == 999
    assert resp["msg_id"] == "m-1"
    assert resp["operation"] == "get_element"


def test_handle_line_unknown_operation():
    proto = Protocol()
    out = proto.handle_line(_line("teleport", {}), {})
    resp = json.loads(out)
    assert resp["error"]["code"] == UNKNOWN_OPERATION_CODE
    assert resp["error"]["message"] == "UNKNOWN_OPERATION"
    assert resp["error"]["details"]["operation"] == "teleport"


def test_handle_line_malformed_json():
    proto = Protocol()
    out = proto.handle_line("{not json", {})
    resp = json.loads(out)
    assert resp["error"]["code"] == UNKNOWN_OPERATION_CODE
    assert resp["error"]["message"] == "MALFORMED_REQUEST"


def test_handle_line_missing_operation_is_unknown():
    proto = Protocol()
    out = proto.handle_line(
        json.dumps({"msg_id": "x", "payload": {}}), {"build_matrix": lambda p: {}}
    )
    resp = json.loads(out)
    assert resp["error"]["code"] == UNKNOWN_OPERATION_CODE


def test_handle_line_handler_receives_empty_payload_when_absent():
    proto = Protocol()
    seen = {}

    def build_matrix(payload):
        seen["payload"] = payload
        return {"snapshot_id": 1}

    out = proto.handle_line(
        json.dumps({"msg_id": "y", "operation": "build_matrix"}),
        {"build_matrix": build_matrix},
    )
    assert json.loads(out)["payload"]["snapshot_id"] == 1
    assert seen["payload"] == {}
