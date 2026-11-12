import base64
from uuid import uuid4

import pytest

from vault.cipher import LocalAesGcmCredentialCipher
from vault.exceptions import CredentialConfigurationError


def test_round_trip_and_random_nonces() -> None:
    cipher = LocalAesGcmCredentialCipher()
    data = b"credential:" + str(uuid4()).encode()
    sealed = cipher.encrypt(b"private password", data)
    assert sealed.startswith(b"v1:")
    assert b"private password" not in sealed
    assert cipher.decrypt(sealed, data) == b"private password"
    assert cipher.encrypt(b"private password", data) != sealed


def test_tamper_detection() -> None:
    cipher = LocalAesGcmCredentialCipher()
    sealed = cipher.encrypt(b"private password", b"credential:one")
    corrupted = sealed[:-1] + bytes([sealed[-1] ^ 1])
    with pytest.raises(CredentialConfigurationError):
        cipher.decrypt(corrupted, b"credential:one")


def test_associated_data_binding() -> None:
    cipher = LocalAesGcmCredentialCipher()
    sealed = cipher.encrypt(b"private password", b"credential:one")
    with pytest.raises(CredentialConfigurationError):
        cipher.decrypt(sealed, b"credential:two")


@pytest.mark.parametrize("key", [None, "", "not-base64", "é", "YWJj", base64.b64encode(b"x" * 33).decode()])
def test_missing_or_malformed_key_fails_closed(key: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    if key is None:
        monkeypatch.delenv("REFUNDOPS_CREDENTIAL_KEY", raising=False)
    else:
        monkeypatch.setenv("REFUNDOPS_CREDENTIAL_KEY", key)
    cipher = LocalAesGcmCredentialCipher()
    for operation in (cipher.encrypt, cipher.decrypt):
        with pytest.raises(CredentialConfigurationError) as caught:
            operation(b"private password", b"credential:one")
        assert "private password" not in str(caught.value)
        if key:
            assert key not in str(caught.value)


def test_key_is_loaded_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFUNDOPS_CREDENTIAL_KEY")
    cipher = LocalAesGcmCredentialCipher()
    monkeypatch.setenv("REFUNDOPS_CREDENTIAL_KEY", base64.b64encode(b"x" * 32).decode())
    assert cipher.decrypt(cipher.encrypt(b"secret", b"id"), b"id") == b"secret"


@pytest.mark.parametrize("payload", [b"", b"v1:", b"v1:short", b"v2:" + b"x" * 40, b"invalid"])
def test_unreadable_payload_fails_closed(payload: bytes) -> None:
    with pytest.raises(CredentialConfigurationError):
        LocalAesGcmCredentialCipher().decrypt(payload, b"credential:one")


def test_wrong_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = LocalAesGcmCredentialCipher().encrypt(b"secret", b"id")
    monkeypatch.setenv("REFUNDOPS_CREDENTIAL_KEY", base64.b64encode(b"z" * 32).decode())
    with pytest.raises(CredentialConfigurationError):
        LocalAesGcmCredentialCipher().decrypt(sealed, b"id")
