from unittest.mock import Mock, patch

import jwt

from utils.auth import _decode_supabase_token, _supabase_jwks_client


def test_supabase_es256_token_uses_project_jwks():
    signing_key = Mock(key="public-key")
    jwks_client = Mock()
    jwks_client.get_signing_key_from_jwt.return_value = signing_key
    _supabase_jwks_client.cache_clear()

    with (
        patch("utils.auth.settings.SUPABASE_URL", "https://example.supabase.co"),
        patch("utils.auth.jwt.get_unverified_header", return_value={"alg": "ES256"}),
        patch("utils.auth.PyJWKClient", return_value=jwks_client) as client_type,
        patch("utils.auth.jwt.decode", return_value={"sub": "user-id"}) as decode,
    ):
        payload = _decode_supabase_token("token")

    client_type.assert_called_once_with(
        "https://example.supabase.co/auth/v1/.well-known/jwks.json"
    )
    jwks_client.get_signing_key_from_jwt.assert_called_once_with("token")
    decode.assert_called_once_with(
        "token",
        "public-key",
        algorithms=["ES256"],
        audience="authenticated",
        issuer="https://example.supabase.co/auth/v1",
    )
    assert payload == {"sub": "user-id"}
    _supabase_jwks_client.cache_clear()


def test_supabase_legacy_hs256_token_uses_shared_secret():
    with (
        patch("utils.auth.settings.SUPABASE_URL", "https://example.supabase.co"),
        patch("utils.auth.settings.SUPABASE_JWT_SECRET", "legacy-secret"),
        patch("utils.auth.jwt.get_unverified_header", return_value={"alg": "HS256"}),
        patch("utils.auth.jwt.decode", return_value={"sub": "user-id"}) as decode,
    ):
        _decode_supabase_token("token")

    decode.assert_called_once_with(
        "token",
        "legacy-secret",
        algorithms=["HS256"],
        audience="authenticated",
        issuer="https://example.supabase.co/auth/v1",
    )


def test_supabase_token_rejects_unknown_algorithm():
    with (
        patch("utils.auth.settings.SUPABASE_URL", "https://example.supabase.co"),
        patch("utils.auth.jwt.get_unverified_header", return_value={"alg": "none"}),
    ):
        try:
            _decode_supabase_token("token")
        except jwt.InvalidAlgorithmError:
            return
    raise AssertionError("Unsupported JWT algorithm was accepted")
