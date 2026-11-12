"""One credential provider per site, routed by the host of the page being filled."""

from uuid import UUID

from navgent.models import CredentialMetadata, CredentialSecret
from vault.domains import normalize_hostname
from vault.exceptions import CredentialDomainDeniedError
from vault.provider import DjangoCredentialProvider


class HostRoutedCredentialProvider:
    """Implements navgent's CredentialProvider for a run that visits several sites.

    Each wrapped provider only releases its credentials on its own host, so a login saved for the portal can never
    be typed into the inbox (or anywhere else).
    """

    def __init__(self, providers: dict[str, DjangoCredentialProvider]) -> None:
        self._providers = {normalize_hostname(host): provider for host, provider in providers.items()}

    async def list_selected(self) -> tuple[CredentialMetadata, ...]:
        selected: list[CredentialMetadata] = []
        for provider in self._providers.values():
            selected.extend(await provider.list_selected())
        return tuple(selected)

    async def resolve(self, credential_id: UUID, domain: str) -> CredentialSecret:
        try:
            provider = self._providers[normalize_hostname(domain)]
        except (KeyError, ValueError):
            raise CredentialDomainDeniedError("No saved login is allowed on this host") from None
        return await provider.resolve(credential_id, domain)
