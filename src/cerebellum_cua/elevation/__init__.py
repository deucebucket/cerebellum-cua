"""Cross-platform elevation handling (polkit GUI / terminal sudo / Windows UAC).

This package lets a task answer a privilege-escalation prompt as part of an
automation flow, with the password sourced from the ``.env`` config
(``CEREBELLUM_ELEVATION_PASSWORD``) — opt-in and sensitive. It is structured as:

* :mod:`~cerebellum_cua.elevation.base` — :class:`ElevationResult` + keywords.
* :mod:`~cerebellum_cua.elevation.detect` — pure prompt detection on window dicts.
* :mod:`~cerebellum_cua.elevation.polkit` — drive a polkit dialog via the engine.
* :mod:`~cerebellum_cua.elevation.sudo` — run a command under ``sudo -S``.
* :mod:`~cerebellum_cua.elevation.uac` — honest Windows UAC (needs a human).

The top-level :func:`elevate` is the single entry point. The password defaults
to :attr:`EnvConfig.elevation_password` and is **never** logged, echoed, or
placed into any returned string — only redacted status flows out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cerebellum_cua.elevation.base import (
    ElevationError,
    ElevationResult,
)
from cerebellum_cua.elevation.detect import (
    find_elevation_prompt,
    is_elevation_prompt,
    prompt_kind,
)
from cerebellum_cua.elevation.polkit import drive_polkit
from cerebellum_cua.elevation.sudo import run_sudo
from cerebellum_cua.elevation.uac import drive_uac, elevate_via_runas
from cerebellum_cua.envconfig import EnvConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

#: Returned when elevation is requested but no password is configured.
_NO_PASSWORD = (
    "no elevation password configured (set CEREBELLUM_ELEVATION_PASSWORD in .env)"
)


def _polkit_prompt_visible(engine: CuaEngine | None) -> bool:
    """True when a polkit auth dialog is currently visible (best-effort).

    Reads authoritative window state from the WM via the ``list_windows``
    operation and runs the pure detector over it. Any failure (no engine, no
    window backend) is treated as 'no prompt visible' rather than raising.
    """
    if engine is None:
        return False
    try:
        windows = engine.handlers["list_windows"]({}).get("windows", [])
    except Exception:  # noqa: BLE001 - detection must never crash elevate()
        return False
    prompt = find_elevation_prompt(windows)
    return prompt is not None and prompt_kind(prompt) == "polkit"


def _resolve_method(
    method: str, engine: CuaEngine | None, command: list[str] | None
) -> str:
    """Resolve ``method="auto"`` to a concrete backend name.

    auto -> ``polkit`` if a polkit prompt is visible (needs an engine), else
    ``sudo`` if a command is given, else ``none`` (a human is needed).
    """
    if method != "auto":
        return method
    if _polkit_prompt_visible(engine):
        return "polkit"
    if command:
        return "sudo"
    return "none"


def elevate(
    method: str = "auto",
    *,
    engine: CuaEngine | None = None,
    command: list[str] | None = None,
    password: str | None = None,
) -> ElevationResult:
    """Answer an elevation prompt, sourcing the password from ``.env`` by default.

    Args:
        method: ``"auto"`` (decide from context), ``"polkit"``, ``"sudo"``, or
            ``"uac"``.
        engine: A live (or mocked) :class:`CuaEngine`, required to drive a polkit
            dialog and to auto-detect a visible polkit prompt.
        command: For ``sudo`` (the argv to run elevated) and for context on the
            ``uac`` path.
        password: Override the credential. Defaults to
            :attr:`EnvConfig.elevation_password`. Never logged or returned.

    Returns:
        An :class:`ElevationResult`. When no password is configured (and the
        chosen method needs one) the result is ``success=False,
        needs_human=True`` with the :data:`_NO_PASSWORD` detail.
    """
    if password is None:
        password = EnvConfig.load().elevation_password

    chosen = _resolve_method(method, engine, command)

    if chosen == "uac":
        return drive_uac(command)
    if chosen == "none":
        return ElevationResult(
            success=False,
            method="none",
            needs_human=True,
            detail="no elevation prompt detected and no command given",
        )

    if password is None:
        return ElevationResult(
            success=False, method=chosen, needs_human=True, detail=_NO_PASSWORD
        )

    if chosen == "polkit":
        if engine is None:
            return ElevationResult(
                success=False,
                method="polkit",
                needs_human=True,
                detail="an engine is required to drive a polkit dialog",
            )
        return drive_polkit(engine, password)
    if chosen == "sudo":
        if not command:
            return ElevationResult(
                success=False,
                method="sudo",
                needs_human=True,
                detail="sudo elevation requires a command to run",
            )
        return run_sudo(command, password)

    return ElevationResult(
        success=False,
        method=chosen,
        needs_human=True,
        detail=f"unknown elevation method {chosen!r}",
    )


def elevate_op(engine: CuaEngine, payload: dict[str, Any]) -> dict[str, Any]:
    """Engine operation handler for ``elevate``.

    Payload: ``{method?: "auto"|"polkit"|"sudo"|"uac", command?: [str, ...]}``.
    The password is **never** accepted via the payload for safety — it is always
    read from the ``.env`` / environment config. Returns
    :meth:`ElevationResult.to_dict`.
    """
    method = str(payload.get("method") or "auto")
    command = payload.get("command")
    cmd_list = [str(c) for c in command] if isinstance(command, list) else None
    result = elevate(method, engine=engine, command=cmd_list)
    return result.to_dict()


__all__ = [
    "elevate",
    "elevate_op",
    "ElevationResult",
    "ElevationError",
    "is_elevation_prompt",
    "find_elevation_prompt",
    "prompt_kind",
    "drive_polkit",
    "run_sudo",
    "drive_uac",
    "elevate_via_runas",
]
