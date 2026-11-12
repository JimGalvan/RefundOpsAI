from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models.signals import pre_delete

from navgent.models import CredentialMetadata
from vault.exceptions import (
    CredentialConfigurationError,
    CredentialDomainDeniedError,
    CredentialInvalidError,
    CredentialNotFoundError,
)
from vault.models import SECRET_FIELDS, Credential
from vault.service import CredentialService

pytestmark = pytest.mark.django_db

VALUES = {
    "login": "private-merchant-user",
    "password": "private-p@ssw0rd-9281",
    "email": "private-mail@example.com",
    "first_name": "Privatefirst",
    "last_name": "Privatelast",
    "phone": "+15551239876",
}


def test_create_metadata_and_no_plaintext_in_row(service: CredentialService) -> None:
    credential = service.create(
        "Merchant", [" HTTPS://EXAMPLE.COM/ ", "example.com"],
        **VALUES, preferred_verification=["email", "email", "sms"], is_default=True,
    )
    assert isinstance(credential.id, type(uuid4()))
    assert credential.allowed_domains == ["example.com"]
    assert credential.available_fields == list(SECRET_FIELDS)
    assert credential.preferred_verification == ["email", "sms"]
    assert credential.created_at is not None
    assert credential.updated_at is not None
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM vault_credential WHERE id = %s", [credential.id.hex])
        row = cursor.fetchone()
    assert row is not None
    for value in VALUES.values():
        assert value not in repr(row)
        assert value.encode() not in bytes(credential.ciphertext)
        assert value not in str(credential)
        assert value not in repr(credential)
    metadata = service.get_metadata(credential.id)
    assert isinstance(metadata, CredentialMetadata)
    assert metadata.available_fields == SECRET_FIELDS
    assert metadata.is_default
    assert not hasattr(metadata, "ciphertext")
    assert not hasattr(metadata, "password")
    secret = service.resolve_for_domain(credential.id, "https://EXAMPLE.com./")
    assert asdict(secret) == VALUES
    assert all(value not in repr(secret) for value in VALUES.values())


@pytest.mark.parametrize(
    ("login", "email", "expected"),
    [("alice@example.com", None, "a***@example.com"), ("alice", None, "a***"),
     ("a", None, "a***"), (None, "user@example.com", "u***@example.com"), (None, None, "")],
)
def test_login_hint(service: CredentialService, login: str | None, email: str | None, expected: str) -> None:
    credential = service.create("Merchant", ["example.com"], login=login, email=email, password="hidden")
    assert credential.login_hint == expected


def test_partial_update_preserves_omitted_fields(service: CredentialService) -> None:
    credential = service.create("Merchant", ["example.com"], **VALUES)
    original_ciphertext = bytes(credential.ciphertext)
    updated = service.update(
        credential.id, name="New merchant", password="replacement-password", email=None,
        allowed_domains=["portal.localhost"], preferred_verification=["sms", "sms"], is_default=True,
    )
    assert updated.name == "New merchant"
    assert updated.is_default
    assert updated.preferred_verification == ["sms"]
    assert updated.updated_at >= credential.updated_at
    assert bytes(updated.ciphertext) != original_ciphertext
    secret = service.resolve_for_domain(credential.id, "portal.localhost")
    assert secret.login == VALUES["login"]
    assert secret.password == "replacement-password"
    assert secret.email is None
    assert "email" not in updated.available_fields
    assert secret.phone == VALUES["phone"]
    with pytest.raises(CredentialDomainDeniedError):
        service.resolve_for_domain(credential.id, "example.com")


def test_update_hint_and_ask_preference(service: CredentialService) -> None:
    credential = service.create("Merchant", ["example.com"], password="secret")
    updated = service.update(credential.id, login="bob@example.com", preferred_verification=["email", "ask"])
    assert updated.login_hint == "b***@example.com"
    assert updated.preferred_verification == []


def test_delete_wipes_before_row_deletion(service: CredentialService) -> None:
    credential = service.create("Merchant", ["example.com"], password="secret")
    observed = []

    def observe_delete(sender: type[Credential], instance: Credential, **kwargs: object) -> None:
        observed.append((bytes(instance.ciphertext), bytes(sender.objects.get(pk=instance.pk).ciphertext)))

    pre_delete.connect(observe_delete, sender=Credential)
    try:
        service.delete(credential.id)
    finally:
        pre_delete.disconnect(observe_delete, sender=Credential)
    assert observed == [(b"", b"")]
    assert not Credential.objects.filter(pk=credential.id).exists()


@pytest.mark.parametrize("operation", ["get_metadata", "delete", "update", "require_selected", "resolve_for_domain"])
@pytest.mark.parametrize("credential_id", [uuid4(), "not-a-uuid"])
def test_unknown_id(service: CredentialService, operation: str, credential_id: object) -> None:
    args = ([credential_id],) if operation == "require_selected" else (credential_id,)
    if operation == "resolve_for_domain":
        args += ("example.com",)
    with pytest.raises(CredentialNotFoundError):
        getattr(service, operation)(*args)


def test_require_selected_is_ordered_deduplicated_and_requires_all(service: CredentialService) -> None:
    first = service.create("First", ["example.com"], password="secret")
    second = service.create("Second", ["example.com"], password="secret")
    selected = service.require_selected([second.id, first.id, str(second.id)])
    assert [item.id for item in selected] == [second.id, first.id]
    with pytest.raises(CredentialNotFoundError):
        service.require_selected([first.id, uuid4()])


def test_copying_ciphertext_to_another_record_fails(service: CredentialService) -> None:
    first = service.create("First", ["example.com"], password="private-password")
    second = service.create("Second", ["example.com"], password="another-password")
    Credential.objects.filter(pk=second.pk).update(ciphertext=first.ciphertext)
    with pytest.raises(CredentialConfigurationError):
        service.resolve_for_domain(second.id, "example.com")


@pytest.mark.parametrize("host", ["evil.com", "example.com.evil.com", "bad/path"])
def test_host_denied_before_decryption(service: CredentialService, host: str) -> None:
    credential = service.create("Merchant", ["example.com"], password="secret")
    Credential.objects.filter(pk=credential.pk).update(ciphertext=b"corrupt")
    with pytest.raises(CredentialDomainDeniedError):
        service.resolve_for_domain(credential.id, host)


@pytest.mark.parametrize("domains", [[], ["*.com"], ["*.github.io"], ["invalid/path"], "example.com"])
def test_create_rejects_invalid_domains(service: CredentialService, domains: object) -> None:
    with pytest.raises(CredentialInvalidError):
        service.create("Merchant", domains, password="private-password")
    assert not Credential.objects.exists()


def test_invalid_secret_updates_do_not_write(service: CredentialService) -> None:
    credential = service.create("Merchant", ["example.com"], password="private-password")
    original = credential.ciphertext
    for fields in ({"password": None}, {"password": 123}, {"unknown": "private-password"}):
        with pytest.raises(CredentialInvalidError) as caught:
            service.update(credential.id, **fields)
        assert "private-password" not in str(caught.value)
        credential.refresh_from_db()
        assert credential.ciphertext == original


def test_no_secret_no_key_and_invalid_name_fail_without_writes(service: CredentialService, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CredentialInvalidError):
        service.create("Merchant", ["example.com"])
    with pytest.raises(CredentialInvalidError):
        service.create("", ["example.com"], password="secret")
    monkeypatch.delenv("REFUNDOPS_CREDENTIAL_KEY")
    with pytest.raises(CredentialConfigurationError):
        service.create("Merchant", ["example.com"], password="secret")
    assert not Credential.objects.exists()


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"password": 12}', b'{"unsupported": "private"}'])
def test_decrypted_payload_is_validated(service: CredentialService, payload: bytes) -> None:
    credential = service.create("Merchant", ["example.com"], password="secret")
    sealed = service._cipher.encrypt(payload, b"credential:" + str(credential.id).encode())
    Credential.objects.filter(pk=credential.pk).update(ciphertext=sealed)
    with pytest.raises(CredentialInvalidError):
        service.resolve_for_domain(credential.id, "example.com")
