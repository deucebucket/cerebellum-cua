"""Canonical error taxonomy (spec Section 4 ``error_codes`` block).

Every protocol-visible failure maps to one of these numeric codes so the JSONL
gateway can emit a uniform ``{"code", "message", "details"}`` error object. Raise
the typed subclass; the protocol layer translates it.
"""

from __future__ import annotations

from typing import Any


class MatrixUIError(Exception):
    """Base for all Cerebellum CUA domain errors. Carries a stable numeric code."""

    code: int = 1000
    message: str = "MATRIX_UI_ERROR"

    def __init__(self, message: str | None = None, **details: Any) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.details: dict[str, Any] = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class SnapshotNotFoundError(MatrixUIError):
    code = 1001
    message = "SNAPSHOT_NOT_FOUND"


class ElementNotFoundError(MatrixUIError):
    code = 1002
    message = "ELEMENT_NOT_FOUND"


class InvalidLazyTokenError(MatrixUIError):
    code = 1003
    message = "INVALID_LAZY_TOKEN"


class TokenExpiredError(MatrixUIError):
    code = 1004
    message = "TOKEN_EXPIRED"


class MaxDepthExceededError(MatrixUIError):
    code = 1005
    message = "MAX_DEPTH_EXCEEDED"


class UIAAccessDeniedError(MatrixUIError):
    code = 1006
    message = "UIA_ACCESS_DENIED"


class DegradedBranchError(MatrixUIError):
    code = 1007
    message = "DEGRADED_BRANCH"


class ConcurrentModificationError(MatrixUIError):
    code = 1008
    message = "CONCURRENT_MODIFICATION"


class TokenBudgetExceededError(MatrixUIError):
    code = 1009
    message = "TOKEN_BUDGET_EXCEEDED"


# Lookup table for code -> class, used by tests and tooling.
ERROR_BY_CODE: dict[int, type[MatrixUIError]] = {
    cls.code: cls
    for cls in (
        SnapshotNotFoundError,
        ElementNotFoundError,
        InvalidLazyTokenError,
        TokenExpiredError,
        MaxDepthExceededError,
        UIAAccessDeniedError,
        DegradedBranchError,
        ConcurrentModificationError,
        TokenBudgetExceededError,
    )
}
