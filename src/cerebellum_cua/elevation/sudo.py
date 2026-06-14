"""Run a command under terminal ``sudo``, feeding the password on stdin.

This is the non-GUI elevation path: run ``sudo -S -p '' <command>`` and write the
password to the process's stdin. ``-S`` makes sudo read the password from stdin;
``-p ''`` suppresses its prompt text so nothing is echoed.

The backend is guarded — if ``sudo`` is not on ``PATH`` (e.g. Windows, or a
minimal container) it returns a failed :class:`ElevationResult` rather than
raising. The password is written only to the subprocess stdin pipe; it is never
logged, never passed as an argument, and never included in the returned detail.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 - sudo invocation is the whole point; args are a list

from cerebellum_cua.elevation.base import ElevationResult

#: Default seconds to wait for the elevated command before giving up.
DEFAULT_TIMEOUT = 30


def _redact(text: str, password: str) -> str:
    """Defensively strip any accidental occurrence of the password from text."""
    if password and password in text:
        return text.replace(password, "***")
    return text


def run_sudo(
    command: list[str],
    password: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> ElevationResult:
    """Run ``command`` under ``sudo -S``, feeding ``password`` on stdin.

    Args:
        command: The argv of the command to run elevated (without a leading
            ``sudo``). Must be a non-empty list of strings.
        password: The sudo password. Written only to the child's stdin; never
            logged or returned.
        timeout: Seconds to wait before killing the command.

    Returns:
        An :class:`ElevationResult`. ``success`` reflects the command's exit
        code 0. ``detail`` is redacted and contains the return code plus a
        redacted snippet of stderr; it never contains the password.
    """
    if not command:
        return ElevationResult(
            success=False, method="sudo", detail="no command given to run under sudo"
        )
    if shutil.which("sudo") is None:
        return ElevationResult(
            success=False,
            method="sudo",
            needs_human=True,
            detail="sudo is not available on this host",
        )

    argv = ["sudo", "-S", "-p", "", *command]
    try:
        completed = subprocess.run(  # noqa: S603 - argv is a list, no shell
            argv,
            input=(password + "\n"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ElevationResult(
            success=False,
            method="sudo",
            needs_human=True,
            detail=f"command timed out after {timeout}s",
            extra={"command_name": command[0]},
        )
    except OSError as exc:
        return ElevationResult(
            success=False,
            method="sudo",
            detail=f"failed to start sudo: {exc.__class__.__name__}",
            extra={"command_name": command[0]},
        )

    stderr = _redact(completed.stderr.strip(), password)
    success = completed.returncode == 0
    detail = (
        f"sudo ran {command[0]!r}: exit {completed.returncode}"
        + (f"; stderr: {stderr[:200]}" if stderr and not success else "")
    )
    return ElevationResult(
        success=success,
        method="sudo",
        needs_human=not success,
        detail=detail,
        extra={"returncode": completed.returncode, "command_name": command[0]},
    )


__all__ = ["run_sudo", "DEFAULT_TIMEOUT"]
