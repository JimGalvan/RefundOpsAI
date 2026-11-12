from collections.abc import Iterable
from uuid import UUID

from asgiref.sync import sync_to_async

from navgent.models import CredentialMetadata, CredentialSecret
from vault.domains import matches_allowlist, normalize_hostname
from vault.exceptions import CredentialDomainDeniedError
from vault.service import CredentialService


class DjangoCredentialProvider:
    def __init__(self, credential_ids: Iterable[UUID | str], target_host: str) -> None:
        self._credential_ids = tuple(UUID(str(value)) for value in credential_ids)
        try:
            self._target_host = normalize_hostname(target_host)
        except ValueError:
            raise CredentialDomainDeniedError("Credential target host is invalid") from None
        self._service = CredentialService()

    async def list_selected(self) -> tuple[CredentialMetadata, ...]:
        selected = await sync_to_async(self._service.require_selected)(self._credential_ids)
        return tuple(
            item for item in selected if matches_allowlist(self._target_host, item.allowed_domains)
        )

    async def resolve(self, credential_id: UUID, domain: str) -> CredentialSecret:
        if credential_id not in self._credential_ids:
            raise ValueError("Credential is not selected for this run")
        try:
            requested_host = normalize_hostname(domain)
        except ValueError:
            raise CredentialDomainDeniedError("Credential target host is invalid") from None
        if requested_host != self._target_host:
            raise CredentialDomainDeniedError("Credentials are restricted to this run's target host")
        return await sync_to_async(self._service.resolve_for_domain)(credential_id, self._target_host)
