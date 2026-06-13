"""JWT lazy-load tokens for the accordion interface (spec Section 4).

A lazy token is an opaque, signed handle issued for one ``(snapshot_id,
parent_row_id)`` pair so a downstream agent can expand exactly that accordion
node later without the gateway re-walking anything. Tokens are HS256-signed with
a 300-second TTL; the payload is ``{sid, pid, max_d, iat, exp}``.

This module is *pure JWT codec logic* — it generates and validates the signed
string. Server-side persistence (the ``lazy_load_tokens`` table, single-use /
410-on-reuse semantics) is the caller's job via the storage backend; the
accordion layer wires the two together.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from cerebellum_cua.errors import InvalidLazyTokenError, TokenExpiredError

#: Default token time-to-live in seconds (spec: 300s server-side validity).
DEFAULT_TTL_SECONDS = 300
_ALGORITHM = "HS256"


class LazyTokenCodec:
    """Encode/decode accordion lazy tokens with a shared HS256 secret.

    The same secret must be used by every gateway process that issues and
    validates tokens for a given snapshot. ``ttl_seconds`` is configurable so
    tests can force fast expiry.
    """

    def __init__(self, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        if not secret:
            raise ValueError("LazyTokenCodec requires a non-empty secret")
        self._secret = secret
        self.ttl_seconds = ttl_seconds

    def generate(self, snapshot_id: int, parent_row_id: int, max_depth: int) -> str:
        """Sign a token for one accordion node. Returns the compact JWT string."""
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sid": snapshot_id,
            "pid": parent_row_id,
            "max_d": max_depth,
            "iat": now.timestamp(),
            "exp": (now + timedelta(seconds=self.ttl_seconds)).timestamp(),
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def decode(
        self,
        token: str,
        expected_sid: int | None = None,
        expected_pid: int | None = None,
    ) -> dict[str, Any]:
        """Validate signature, expiry, and (optionally) the bound sid/pid.

        Raises :class:`TokenExpiredError` if the token's ``exp`` has passed and
        :class:`InvalidLazyTokenError` for any malformed, mis-signed, or
        mismatched token.
        """
        try:
            decoded: dict[str, Any] = jwt.decode(
                token, self._secret, algorithms=[_ALGORITHM]
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError(reason="token_expired") from exc
        except jwt.PyJWTError as exc:
            raise InvalidLazyTokenError(
                reason="decode_failed", error=str(exc)
            ) from exc

        if expected_sid is not None and decoded.get("sid") != expected_sid:
            raise InvalidLazyTokenError(
                reason="snapshot_mismatch",
                expected=expected_sid,
                actual=decoded.get("sid"),
            )
        if expected_pid is not None and decoded.get("pid") != expected_pid:
            raise InvalidLazyTokenError(
                reason="parent_mismatch",
                expected=expected_pid,
                actual=decoded.get("pid"),
            )
        return decoded
