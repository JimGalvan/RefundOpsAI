import json
from dataclasses import asdict

import pytest
from django.contrib.admin.models import LogEntry
from django.test import Client
from django.urls import reverse

from vault.admin import CredentialAdminForm
from vault.models import SECRET_FIELDS, Credential
from vault.service import CredentialService

pytestmark = pytest.mark.django_db

SECRETS = {field: f"private-{field}-782946" for field in SECRET_FIELDS}


def form_data(**changes: object) -> dict[str, object]:
    return {
        "name": "Merchant", "allowed_domains": json.dumps(["portal.localhost"]),
        "preferred_verification": "[]", **changes,
    }


def test_existing_form_never_decrypts_or_renders_secrets(service: CredentialService, monkeypatch: pytest.MonkeyPatch) -> None:
    credential = service.create("Merchant", ["portal.localhost"], **SECRETS)

    def fail_decrypt(*args: object, **kwargs: object) -> None:
        pytest.fail("Rendering an admin form must not decrypt credentials")

    monkeypatch.setattr(CredentialService, "_decrypt", fail_decrypt)
    form = CredentialAdminForm(instance=credential)
    rendered = form.as_p()
    assert "ciphertext" not in form.fields
    for field, value in SECRETS.items():
        assert value not in rendered
        assert not form[field].value()
        assert form.fields[field].widget.input_type == "password"
        assert not form.fields[field].widget.render_value


def test_bound_form_never_echoes_submitted_secrets() -> None:
    form = CredentialAdminForm(data=form_data(name="", **SECRETS))
    assert not form.is_valid()
    rendered = form.as_p()
    assert all(value not in rendered for value in SECRETS.values())


def test_form_create_and_blank_update_keep_saved_values(service: CredentialService) -> None:
    form = CredentialAdminForm(data=form_data(**SECRETS))
    assert form.is_valid(), form.errors
    credential = form.save()
    assert asdict(service.resolve_for_domain(credential.id, "portal.localhost")) == SECRETS
    update = CredentialAdminForm(instance=credential, data=form_data(name="Renamed", **dict.fromkeys(SECRET_FIELDS, "")))
    assert update.is_valid(), update.errors
    updated = update.save()
    assert updated.name == "Renamed"
    assert asdict(service.resolve_for_domain(credential.id, "portal.localhost")) == SECRETS


def test_form_update_with_commit_false_encrypts_before_save(service: CredentialService) -> None:
    credential = service.create("Merchant", ["portal.localhost"], **SECRETS)
    form = CredentialAdminForm(instance=credential, data=form_data(password="replacement-secret"))
    assert form.is_valid(), form.errors
    prepared = form.save(commit=False)
    assert service.resolve_for_domain(credential.id, "portal.localhost").password == SECRETS["password"]
    prepared.save()
    secret = service.resolve_for_domain(credential.id, "portal.localhost")
    assert secret.password == "replacement-secret"
    assert secret.login == SECRETS["login"]


@pytest.mark.parametrize("changes", [{}, {"password": "secret", "allowed_domains": '["*.com"]'}, {"password": "secret", "preferred_verification": '"email"'}])
def test_invalid_admin_form(changes: dict[str, object]) -> None:
    assert not CredentialAdminForm(data=form_data(**changes)).is_valid()


def test_admin_change_list_and_change_page_hide_secrets(admin_client: Client, service: CredentialService) -> None:
    credential = service.create("Merchant", ["portal.localhost"], **SECRETS)
    for url in (reverse("admin:vault_credential_changelist"), reverse("admin:vault_credential_change", args=[credential.pk])):
        response = admin_client.get(url)
        assert response.status_code == 200
        rendered = response.content.decode()
        assert all(value not in rendered for value in SECRETS.values())
        assert bytes(credential.ciphertext).hex() not in rendered
        assert "ciphertext" not in rendered


def test_admin_add_post_encrypts_and_does_not_log_values(admin_client: Client, service: CredentialService) -> None:
    response = admin_client.post(reverse("admin:vault_credential_add"), form_data(**SECRETS))
    assert response.status_code == 302
    credential = Credential.objects.get()
    assert asdict(service.resolve_for_domain(credential.id, "portal.localhost")) == SECRETS
    assert all(value not in repr(list(LogEntry.objects.values())) for value in SECRETS.values())
