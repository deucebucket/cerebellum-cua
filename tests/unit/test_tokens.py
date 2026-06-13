"""Unit tests for the JWT lazy-token codec (gateway/tokens.py).

Covers a sign/verify round-trip, sid/pid binding mismatch detection, expiry
(via a zero-TTL codec), and tampered-token rejection.
"""

from __future__ import annotations

import pytest

from cerebellum_cua.errors import InvalidLazyTokenError, TokenExpiredError
from cerebellum_cua.gateway.tokens import LazyTokenCodec


def test_round_trip_decodes_payload():
    codec = LazyTokenCodec("s3cr3t")
    token = codec.generate(snapshot_id=47, parent_row_id=17, max_depth=2)
    decoded = codec.decode(token)
    assert decoded["sid"] == 47
    assert decoded["pid"] == 17
    assert decoded["max_d"] == 2
    assert decoded["exp"] > decoded["iat"]


def test_decode_validates_expected_sid_and_pid():
    codec = LazyTokenCodec("s3cr3t")
    token = codec.generate(47, 17, 2)
    # Correct binding passes.
    assert codec.decode(token, expected_sid=47, expected_pid=17)["pid"] == 17


def test_decode_rejects_snapshot_mismatch():
    codec = LazyTokenCodec("s3cr3t")
    token = codec.generate(47, 17, 2)
    with pytest.raises(InvalidLazyTokenError) as exc:
        codec.decode(token, expected_sid=99)
    assert exc.value.details["reason"] == "snapshot_mismatch"


def test_decode_rejects_parent_mismatch():
    codec = LazyTokenCodec("s3cr3t")
    token = codec.generate(47, 17, 2)
    with pytest.raises(InvalidLazyTokenError) as exc:
        codec.decode(token, expected_pid=18)
    assert exc.value.details["reason"] == "parent_mismatch"


def test_expired_token_raises_token_expired():
    codec = LazyTokenCodec("s3cr3t", ttl_seconds=0)
    token = codec.generate(47, 17, 2)
    with pytest.raises(TokenExpiredError):
        codec.decode(token)


def test_tampered_token_raises_invalid():
    codec = LazyTokenCodec("s3cr3t")
    token = codec.generate(47, 17, 2)
    # Flip a character in the signature segment.
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}.{sig[:-2]}XX"
    with pytest.raises(InvalidLazyTokenError):
        codec.decode(tampered)


def test_wrong_secret_raises_invalid():
    token = LazyTokenCodec("right-secret").generate(1, 0, 2)
    with pytest.raises(InvalidLazyTokenError):
        LazyTokenCodec("wrong-secret").decode(token)


def test_empty_secret_rejected():
    with pytest.raises(ValueError):
        LazyTokenCodec("")
