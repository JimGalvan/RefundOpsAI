from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async

from navgent.credentials import CredentialProvider
from vault.exceptions import CredentialDomainDeniedError, CredentialNotFoundError
from vault.provider import DjangoCredentialProvider
from vault.service import CredentialService

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


async def test_list_selected_filters_host_and_returns_only_metadata(service: CredentialService) -> None:
    allowed = await sync_to_async(service.create)("Portal", ["portal.localhost"], password="secret")
    other = await sync_to_async(service.create)("Tracking", ["tracking.localhost"], password="secret")
    wildcard = await sync_to_async(service.create)("Demo", ["*.localhost"], login="private-login")
    await sync_to_async(service.create)("Unselected", ["*.localhost"], password="secret")
    provider: CredentialProvider = DjangoCredentialProvider([allowed.id, other.id, wildcard.id], "PORTAL.localhost.")
    selected = await provider.list_selected()
    assert isinstance(selected, tuple)
    assert [item.id for item in selected] == [allowed.id, wildcard.id]
    assert all(not hasattr(item, "password") and not hasattr(item, "ciphertext") for item in selected)


async def test_resolve_runs_async_and_checks_selection_and_host(service: CredentialService) -> None:
    credential = await sync_to_async(service.create)("Portal", ["*.localhost"], password="secret")
    provider = DjangoCredentialProvider([credential.id], "portal.localhost")
    assert (await provider.resolve(credential.id, "PORTAL.localhost.")).password == "secret"
    with pytest.raises(CredentialDomainDeniedError):
        await provider.resolve(credential.id, "tracking.localhost")
    with pytest.raises(CredentialDomainDeniedError):
        await provider.resolve(credential.id, "invalid/path")
    with pytest.raises(ValueError, match="not selected"):
        await provider.resolve(uuid4(), "portal.localhost")


async def test_resolve_rechecks_changed_allowlist(service: CredentialService) -> None:
    credential = await sync_to_async(service.create)("Portal", ["portal.localhost"], password="secret")
    provider = DjangoCredentialProvider([credential.id], "portal.localhost")
    assert len(await provider.list_selected()) == 1
    await sync_to_async(service.update)(credential.id, allowed_domains=["tracking.localhost"])
    assert await provider.list_selected() == ()
    with pytest.raises(CredentialDomainDeniedError):
        await provider.resolve(credential.id, "portal.localhost")


async def test_missing_selected_credentials_are_reported() -> None:
    missing_id = uuid4()
    provider = DjangoCredentialProvider([missing_id], "portal.localhost")
    with pytest.raises(CredentialNotFoundError):
        await provider.list_selected()
    with pytest.raises(CredentialNotFoundError):
        await provider.resolve(missing_id, "portal.localhost")


async def test_idna_target_host(service: CredentialService) -> None:
    credential = await sync_to_async(service.create)("IDNA", ["bücher.example"], password="secret")
    provider = DjangoCredentialProvider([credential.id], "BÜCHER.example")
    assert len(await provider.list_selected()) == 1
    assert (await provider.resolve(credential.id, "xn--bcher-kva.example")).password == "secret"
