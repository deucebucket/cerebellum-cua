"""Windows UAC handling — honest about the secure-desktop limit.

The Windows User Account Control (UAC) consent dialog runs, by default, on the
**secure desktop** (``winsta0\\Winlogon``), an isolated desktop that ordinary
processes cannot read or send input to. A normal automation tool — including this
one — therefore *cannot* drive the UAC prompt: it cannot capture its UI tree and
cannot synthesize a click on it. This is a deliberate OS security boundary, not a
gap we can paper over.

There are only two honest ways for a tool to get past UAC:

* The tool's own process is already elevated (so the OS does not raise UAC for
  child actions), or it holds the ``uiAccess`` privilege (a signed manifest +
  install under ``Program Files``), which lets it interact with the secure
  desktop. Neither is something this library can grant itself at runtime.
* The administrator disables the secure desktop
  (``PromptOnSecureDesktop = 0``) and lowers ``ConsentPromptBehaviorAdmin`` —
  a system-wide security downgrade we will not perform or recommend.

So this module does **not** pretend to click UAC. It documents the limit and
returns ``needs_human=True``. :func:`elevate_via_runas` describes the one
best-effort, non-secure-desktop option (relaunching elevated via ``runas`` /
``ShellExecute "runas"``), which still surfaces a UAC prompt a human must accept.
"""

from __future__ import annotations

from cerebellum_cua.elevation.base import ElevationResult

#: The factual reason driving UAC from a normal process is not possible.
_SECURE_DESKTOP_NOTE = (
    "UAC consent runs on the isolated secure desktop, which a non-elevated "
    "process without uiAccess cannot capture or click. A human must accept the "
    "prompt, or the tool itself must run elevated / with uiAccess."
)


def elevate_via_runas(command: list[str] | None = None) -> ElevationResult:
    """Describe the best-effort ``runas`` relaunch path (still needs a human).

    Relaunching a command elevated via ``ShellExecuteEx`` with the ``"runas"``
    verb is the only sanctioned non-secure-desktop option, but it *still* raises
    a UAC consent dialog that a person must approve — the password/credential
    cannot be injected programmatically. This function does not attempt the
    relaunch (it would block on human consent and is Windows-only); it returns a
    result documenting the path and that a human is required.

    Args:
        command: The command that would be relaunched elevated, for context.

    Returns:
        An :class:`ElevationResult` with ``needs_human=True``.
    """
    cmd_name = command[0] if command else None
    return ElevationResult(
        success=False,
        method="uac",
        needs_human=True,
        detail=(
            "runas relaunch would still require a human to accept the UAC "
            "prompt; credentials cannot be injected. " + _SECURE_DESKTOP_NOTE
        ),
        extra={"command_name": cmd_name, "path": "runas"},
    )


def drive_uac(command: list[str] | None = None) -> ElevationResult:
    """Return the honest UAC result: a human must complete the consent prompt.

    Args:
        command: The command requiring elevation, for context only.

    Returns:
        An :class:`ElevationResult` with ``success=False`` and
        ``needs_human=True``, carrying the secure-desktop explanation.
    """
    cmd_name = command[0] if command else None
    return ElevationResult(
        success=False,
        method="uac",
        needs_human=True,
        detail=_SECURE_DESKTOP_NOTE,
        extra={"command_name": cmd_name},
    )


__all__ = ["drive_uac", "elevate_via_runas"]
