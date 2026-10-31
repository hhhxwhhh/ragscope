"""Webhook signing and payload helpers."""

import json

from app.services.webhook_service import new_secret, sign


def test_signature_is_deterministic_and_keyed():
    body = json.dumps({"event": "sync.completed"}, separators=(",", ":")).encode()
    first = sign("secret-a", body)
    assert first == sign("secret-a", body)
    assert first != sign("secret-b", body)
    assert first.startswith("sha256=")
    # 7 chars of prefix + 64 hex chars
    assert len(first) == 7 + 64


def test_signature_changes_with_body():
    assert sign("s", b'{"a":1}') != sign("s", b'{"a":2}')


def test_new_secret_is_url_safe_and_unique():
    first, second = new_secret(), new_secret()
    assert first != second
    assert len(first) == 48
    assert all(ch in "0123456789abcdef" for ch in first)
