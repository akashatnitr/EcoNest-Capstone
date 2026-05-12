"""Tests for security utilities."""

from datetime import timedelta

from orchestrator.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_refresh_token,
)


def test_password_hash_and_verify() -> None:
    hashed = hash_password("secret")

    assert hashed != "secret"
    assert verify_password("secret", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_handles_invalid_hash() -> None:
    assert not verify_password("secret", "not-a-bcrypt-hash")


def test_access_token_contains_standard_claims() -> None:
    token = create_access_token({"sub": "1", "role": "homeowner"})

    payload = decode_token(token, expected_type="access")

    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["role"] == "homeowner"
    assert payload["type"] == "access"
    assert "iat" in payload
    assert "jti" in payload
    assert "exp" in payload


def test_decode_token_rejects_wrong_type() -> None:
    token = create_refresh_token({"sub": "1"})

    assert decode_token(token, expected_type="access") is None
    assert decode_token(token, expected_type="refresh") is not None


def test_decode_token_rejects_expired_token() -> None:
    token = create_access_token({"sub": "1"}, expires_delta=timedelta(seconds=-1))

    assert decode_token(token) is None


def test_refresh_token_hashing() -> None:
    token = create_refresh_token({"sub": "1"})
    token_hash = hash_refresh_token(token)

    assert token_hash != token
    assert verify_refresh_token(token, token_hash)
    assert not verify_refresh_token("different-token", token_hash)
