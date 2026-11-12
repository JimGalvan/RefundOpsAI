from io import StringIO
from uuid import UUID

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from vault.models import Credential
from vault.service import CredentialService

pytestmark = pytest.mark.django_db


def test_vault_add_prompts_for_password_and_prints_only_id(monkeypatch: pytest.MonkeyPatch, service: CredentialService) -> None:
    prompts = []

    def password_prompt(prompt: str) -> str:
        prompts.append(prompt)
        return "private-command-password"

    monkeypatch.setattr("vault.management.commands.vault_add.getpass", password_prompt)
    stdout = StringIO()
    call_command("vault_add", name="Merchant", domains="portal.localhost,tracking.localhost", login="private-login", email="private@example.com", stdout=stdout)
    credential_id = UUID(stdout.getvalue().strip())
    assert stdout.getvalue() == f"{credential_id}\n"
    assert prompts == ["Password: "]
    secret = service.resolve_for_domain(credential_id, "portal.localhost")
    assert secret.password == "private-command-password"
    assert secret.email == "private@example.com"
    assert Credential.objects.get(pk=credential_id).allowed_domains == ["portal.localhost", "tracking.localhost"]


def test_vault_add_has_no_password_argument() -> None:
    with pytest.raises(CommandError, match="unrecognized arguments"):
        call_command("vault_add", "--name", "Merchant", "--domains", "portal.localhost", "--login", "user", "--password", "secret")


def test_vault_add_missing_key_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFUNDOPS_CREDENTIAL_KEY")
    monkeypatch.setattr("vault.management.commands.vault_add.getpass", lambda _: "private-password")
    stdout = StringIO()
    with pytest.raises(CommandError) as caught:
        call_command("vault_add", name="Merchant", domains="portal.localhost", login="private-login", stdout=stdout)
    assert "private-password" not in str(caught.value)
    assert "private-login" not in str(caught.value)
    assert stdout.getvalue() == ""
    assert not Credential.objects.exists()
