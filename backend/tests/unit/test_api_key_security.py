"""API key generation, hashing and scope rules (no database required)."""

from datetime import UTC, datetime, timedelta

from app.core.security import (
    API_KEY_PREFIX_LENGTH,
    api_key_matches,
    generate_api_key,
    hash_api_key,
    looks_like_api_key,
)
from app.services.api_key_service import SCOPES, has_scope, validate_scopes


def test_generated_key_is_prefixed_and_not_stored_in_plaintext():
    plaintext, prefix, digest = generate_api_key()

    assert plaintext.startswith("rk_")
    assert prefix == plaintext[:API_KEY_PREFIX_LENGTH]
    assert len(digest) == 64
    assert plaintext not in digest
    assert digest == hash_api_key(plaintext)


def test_keys_are_unique_and_only_match_their_own_hash():
    first, _, first_hash = generate_api_key()
    second, _, second_hash = generate_api_key()

    assert first != second
    assert api_key_matches(first, first_hash)
    assert not api_key_matches(second, first_hash)
    assert not api_key_matches(first, second_hash)


def test_prefix_detection_distinguishes_jwt_from_api_key():
    assert looks_like_api_key("rk_" + "a" * 30)
    assert not looks_like_api_key("eyJhbGciOiJIUzI1NiJ9.payload.signature")
    assert not looks_like_api_key("rk_short")


def test_validate_scopes_rejects_unknown_and_empty():
    assert validate_scopes(["chat", "kb:read", "chat"]) == ["chat", "kb:read"]
    for bad in ([], ["kb:delete"]):
        try:
            validate_scopes(bad)
        except Exception as exc:  # ForbiddenError
            assert "scope" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected rejection")


def test_admin_scope_implies_every_scope():
    class Key:
        scopes = ["admin"]

    assert has_scope(Key(), "kb:write")
    assert has_scope(Key(), "chat")

    class Reader:
        scopes = ["kb:read"]

    assert has_scope(Reader(), "kb:read")
    assert not has_scope(Reader(), "chat")
    assert set(SCOPES) == {"kb:read", "kb:write", "chat", "admin"}


def test_is_usable_flags_revoked_and_expired():
    from app.services.api_key_service import is_usable

    class Key:
        def __init__(self, revoked=None, expires=None):
            self.revoked_at = revoked
            self.expires_at = expires

    now = datetime.now(UTC)
    assert is_usable(Key())[0] is True
    assert is_usable(Key(revoked=now)) == (False, "revoked")
    assert is_usable(Key(expires=now - timedelta(minutes=1))) == (False, "expired")
    assert is_usable(Key(expires=now + timedelta(days=1)))[0] is True
