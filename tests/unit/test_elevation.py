"""Unit tests for the cross-platform elevation layer (Linux-testable, no real sudo/UAC).

Covers:

* pure prompt detection (:func:`is_elevation_prompt` / :func:`find_elevation_prompt`)
  on hand-built window dicts — polkit, UAC, and non-matches;
* :func:`drive_polkit` with a fully mocked engine — asserts the password is
  set into the field and the Authenticate button clicked, and never appears in
  the returned detail;
* :func:`run_sudo` with a mocked ``subprocess.run`` — password fed on stdin,
  redacted detail, and the guarded sudo-missing path;
* the no-password and honest-UAC paths return ``needs_human``;
* the ``elevate`` operation routed through ``handle_line``.

No test prints or asserts a real password value into any output channel.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import cerebellum_cua.elevation.sudo as sudo_mod
from cerebellum_cua.elevation import (
    ElevationResult,
    elevate,
    find_elevation_prompt,
    is_elevation_prompt,
    prompt_kind,
)
from cerebellum_cua.elevation.polkit import drive_polkit
from cerebellum_cua.elevation.sudo import run_sudo
from cerebellum_cua.elevation.uac import drive_uac, elevate_via_runas
from cerebellum_cua.model import BoundingRect, Element

# A sentinel password used everywhere; assert it never leaks into output.
SECRET_PW = "hunter2-do-not-leak"


# --- detection (pure) ----------------------------------------------------
def test_is_elevation_prompt_polkit_by_title() -> None:
    assert is_elevation_prompt({"title": "Authentication Required", "app": "polkit"})
    assert is_elevation_prompt({"title": "Authenticate"})
    assert prompt_kind({"title": "Authentication Required"}) == "polkit"


def test_is_elevation_prompt_uac() -> None:
    win = {"title": "User Account Control", "app": "consent.exe"}
    assert is_elevation_prompt(win)
    assert prompt_kind(win) == "uac"


def test_is_elevation_prompt_case_insensitive_and_role() -> None:
    assert is_elevation_prompt({"title": "POLKIT1 dialog"})
    assert is_elevation_prompt({"role": "Authentication", "title": ""})


def test_non_match_is_not_a_prompt() -> None:
    assert not is_elevation_prompt({"title": "Firefox", "app": "firefox"})
    assert not is_elevation_prompt({})
    assert prompt_kind({"title": "Calculator"}) is None
    assert not is_elevation_prompt("not a dict")  # type: ignore[arg-type]


def test_find_elevation_prompt_returns_first_match() -> None:
    windows = [
        {"title": "Terminal", "app": "konsole"},
        {"title": "Authenticate", "app": "polkit"},
        {"title": "User Account Control"},
    ]
    found = find_elevation_prompt(windows)
    assert found is not None and found["app"] == "polkit"
    assert find_elevation_prompt([{"title": "nope"}]) is None
    assert find_elevation_prompt([]) is None


# --- drive_polkit (mocked engine) ----------------------------------------
class _FakeStorage:
    """Minimal storage stub returning a fixed element list."""

    def __init__(self, elements: list[Element]) -> None:
        self._elements = elements

    def get_all_elements(self, snapshot_id: int) -> list[Element]:
        return self._elements

    def get_semantic_concepts(self, snapshot_id: int, row_id: int) -> list[Any]:
        return []


class _FakeEngine:
    """Mock engine recording invoke_action calls; no live capture."""

    def __init__(self, elements: list[Element]) -> None:
        self.storage = _FakeStorage(elements)
        self.calls: list[dict[str, Any]] = []
        self.handlers = {
            "build_matrix": self._build_matrix,
            "invoke_action": self._invoke_action,
            "list_windows": lambda payload: {"windows": []},
        }

    def _build_matrix(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"snapshot_id": 1, "epoch": 1, "status": "success"}

    def _invoke_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(payload))
        return {"success": True, "action": payload.get("action")}


def _el(row_id: int, name: str, ct: int) -> Element:
    return Element(
        row_id=row_id,
        control_type=ct,
        name=name,
        bounding_rect=BoundingRect(left=0, top=row_id * 10, width=10, height=10),
    )


def _auth_dialog_elements() -> list[Element]:
    return [
        _el(0, "Password", 50004),  # EDIT field
        _el(1, "Cancel", 50000),
        _el(2, "Authenticate", 50000),
    ]


def test_drive_polkit_sets_password_and_clicks_authenticate() -> None:
    engine = _FakeEngine(_auth_dialog_elements())
    result = drive_polkit(engine, SECRET_PW)  # type: ignore[arg-type]

    assert result.success is True
    assert result.method == "polkit"
    # The password was set into the EDIT field (row 0) via set_text...
    set_calls = [c for c in engine.calls if c["action"] == "set_text"]
    assert len(set_calls) == 1
    assert set_calls[0]["row_id"] == 0
    assert set_calls[0]["value"] == SECRET_PW
    # ...and the Authenticate button (row 2) was clicked.
    click_calls = [c for c in engine.calls if c["action"] == "click"]
    assert click_calls and click_calls[0]["row_id"] == 2
    # The password must NOT appear in any returned/serialized string.
    assert SECRET_PW not in json.dumps(result.to_dict())


def test_drive_polkit_no_password_field() -> None:
    engine = _FakeEngine([_el(0, "OK", 50000)])  # no EDIT field
    result = drive_polkit(engine, SECRET_PW)  # type: ignore[arg-type]
    assert result.success is False
    assert result.needs_human is True
    assert SECRET_PW not in json.dumps(result.to_dict())


# --- run_sudo (mocked subprocess) ----------------------------------------
class _Completed:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_run_sudo_feeds_password_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> _Completed:
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _Completed(0)

    monkeypatch.setattr(sudo_mod.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(sudo_mod.subprocess, "run", fake_run)

    result = run_sudo(["whoami"], SECRET_PW)
    assert result.success is True
    assert result.method == "sudo"
    # Password fed on stdin (with newline), not as an argument.
    assert captured["input"] == SECRET_PW + "\n"
    assert SECRET_PW not in " ".join(captured["argv"])
    assert captured["argv"][:4] == ["sudo", "-S", "-p", ""]
    # Detail is redacted: no password leak.
    assert SECRET_PW not in json.dumps(result.to_dict())


def test_run_sudo_redacts_password_in_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    # A misbehaving sudo that echoes the password into stderr must be redacted.
    def fake_run(argv: list[str], **kwargs: Any) -> _Completed:
        return _Completed(1, stderr=f"bad attempt: {SECRET_PW}")

    monkeypatch.setattr(sudo_mod.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(sudo_mod.subprocess, "run", fake_run)

    result = run_sudo(["whoami"], SECRET_PW)
    assert result.success is False
    assert result.needs_human is True
    assert SECRET_PW not in json.dumps(result.to_dict())


def test_run_sudo_guarded_when_sudo_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sudo_mod.shutil, "which", lambda name: None)
    result = run_sudo(["whoami"], SECRET_PW)
    assert result.success is False
    assert result.needs_human is True
    assert "not available" in result.detail


def test_run_sudo_empty_command() -> None:
    result = run_sudo([], SECRET_PW)
    assert result.success is False
    assert "no command" in result.detail


# --- no-password / UAC honest paths --------------------------------------
def test_elevate_no_password_needs_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # Force EnvConfig to resolve no elevation password: clear env + cwd with no .env.
    monkeypatch.delenv("CEREBELLUM_ELEVATION_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)
    result = elevate("sudo", command=["whoami"], password=None)
    assert result.detail.startswith("no elevation password")
    assert result.needs_human is True
    assert result.success is False


def test_elevate_explicit_no_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.delenv("CEREBELLUM_ELEVATION_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)
    result = elevate("polkit", engine=None, password=None)
    assert result.needs_human is True
    assert result.success is False


def test_uac_is_honest_needs_human() -> None:
    result = drive_uac(["installer.exe"])
    assert result.method == "uac"
    assert result.needs_human is True
    assert result.success is False
    assert "secure desktop" in result.detail.lower()

    runas = elevate_via_runas(["installer.exe"])
    assert runas.needs_human is True
    assert runas.success is False


def test_elevate_uac_method() -> None:
    result = elevate("uac", command=["foo.exe"], password=SECRET_PW)
    assert result.method == "uac"
    assert result.needs_human is True
    assert SECRET_PW not in json.dumps(result.to_dict())


def test_elevate_auto_no_prompt_no_command() -> None:
    result = elevate("auto", engine=None, command=None, password=SECRET_PW)
    assert result.method == "none"
    assert result.needs_human is True


# --- elevate op via handle_line ------------------------------------------
def test_elevate_op_via_handle_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from cerebellum_cua.cli import CuaEngine

    monkeypatch.delenv("CEREBELLUM_ELEVATION_PASSWORD", raising=False)
    engine = CuaEngine(db_dsn=None, secret="unit-test-secret")
    try:
        request = json.dumps(
            {"msg_id": "m1", "operation": "elevate",
             "payload": {"method": "uac", "command": ["x.exe"]}}
        )
        response = json.loads(engine.handle_line(request))
        assert response["operation"] == "elevate"
        assert response["error"] is None
        payload = response["payload"]
        assert payload["method"] == "uac"
        assert payload["needs_human"] is True
        assert "success" in payload and "detail" in payload
        # Whatever happened, no password ever appears on the wire.
        assert SECRET_PW not in json.dumps(response)
    finally:
        engine.close()


def test_elevation_result_to_dict_shape() -> None:
    res = ElevationResult(success=True, method="polkit", detail="ok")
    d = res.to_dict()
    assert set(d) == {"success", "method", "needs_human", "detail", "extra"}
    assert d["success"] is True and d["method"] == "polkit"
