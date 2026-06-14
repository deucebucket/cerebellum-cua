"""Shared contracts for the elevation layer (result type, error, keywords).

Elevation handling lets a task answer a privilege-escalation prompt — a Linux
polkit authentication dialog, an interactive terminal ``sudo``, or (on Windows)
the UAC consent dialog — using a password sourced from the ``.env`` config.

This module holds only the dependency-light pieces every elevation backend
shares: a typed error, a serializable :class:`ElevationResult`, and the keyword
constants used to recognize an elevation prompt. It imports nothing from the
other elevation modules so any of them (and any test) can use it.

The password is sensitive. Nothing in this layer logs, echoes, or stores it in a
result/detail string — :class:`ElevationResult` carries only redacted status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Substrings (case-insensitive) that mark a polkit / Linux auth prompt by
#: title, app name, or accessible role. Matching any one is sufficient.
POLKIT_KEYWORDS: tuple[str, ...] = (
    "authenticate",
    "authentication required",
    "authentication",
    "polkit",
    "polkit1",
    "authentication is required",
    "superuser",
)

#: Substrings (case-insensitive) that mark a Windows UAC consent prompt.
UAC_KEYWORDS: tuple[str, ...] = (
    "user account control",
    "consent",
    "consentui",
    "do you want to allow this app",
)

#: Accessible roles a password field commonly exposes (kept here so detect.py
#: and polkit.py agree on the same vocabulary).
PASSWORD_FIELD_ROLES: tuple[str, ...] = ("edit", "password_text", "passwordtext")

#: Button labels (case-insensitive) that confirm / submit an auth dialog.
CONFIRM_LABELS: tuple[str, ...] = (
    "authenticate",
    "ok",
    "yes",
    "unlock",
    "continue",
    "confirm",
)


class ElevationError(RuntimeError):
    """Raised when an elevation backend fails in a way the caller must handle.

    Backends prefer returning an :class:`ElevationResult` with ``success=False``
    for expected, recoverable conditions (no password configured, prompt not
    found, UAC secure-desktop limit). This error is reserved for genuinely
    unexpected failures. Its message must never contain the password.
    """


@dataclass(slots=True)
class ElevationResult:
    """Outcome of an elevation attempt.

    Attributes:
        success: Whether elevation was driven/granted successfully.
        method: Which backend ran (``"polkit"`` / ``"sudo"`` / ``"uac"`` /
            ``"none"``).
        needs_human: True when no automated path can proceed and a person must
            complete the prompt (e.g. UAC secure desktop, or no password set).
        detail: A short, **redacted** human-readable status. The password must
            never appear here.
        extra: Optional non-sensitive structured context (return code, prompt
            window id, …). Never put the password in here.
    """

    success: bool
    method: str
    needs_human: bool = False
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for the protocol envelope."""
        return {
            "success": self.success,
            "method": self.method,
            "needs_human": self.needs_human,
            "detail": self.detail,
            "extra": dict(self.extra),
        }


__all__ = [
    "ElevationError",
    "ElevationResult",
    "POLKIT_KEYWORDS",
    "UAC_KEYWORDS",
    "PASSWORD_FIELD_ROLES",
    "CONFIRM_LABELS",
]
