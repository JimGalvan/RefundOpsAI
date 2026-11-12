import json
from collections.abc import Iterable
from dataclasses import asdict
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from navgent.models import CredentialMetadata, CredentialSecret
from vault.cipher import CredentialCipher, LocalAesGcmCredentialCipher
from vault.domains import (
    matches_allowlist,
    normalize_allowlist_entry,
    normalize_hostname,
)
from vault.exceptions import (
    CredentialDomainDeniedError,
    CredentialInvalidError,
    CredentialNotFoundError,
)
from vault.models import SECRET_FIELDS, Credential


class CredentialService:
    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self._cipher = cipher if cipher is not None else LocalAesGcmCredentialCipher()

    def create(
        self,
        name: str,
        allowed_domains: Iterable[str],
        *,
        login: str | None = None,
        password: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        preferred_verification: Iterable[str] = (),
        is_default: bool = False,
    ) -> Credential:
        credential = Credential(
            name=self._normalize_name(name),
            allowed_domains=self._normalize_domains(allowed_domains),
            preferred_verification=self._normalize_verification(preferred_verification),
            is_default=is_default,
        )
        secret = CredentialSecret(login, password, email, first_name, last_name, phone)
        self._write_secret(credential, secret)
        credential.save(force_insert=True)
        return credential

    @transaction.atomic
    def update(
        self,
        credential_id: UUID | str,
        *,
        name: str | None = None,
        allowed_domains: Iterable[str] | None = None,
        preferred_verification: Iterable[str] | None = None,
        is_default: bool | None = None,
        **secret_fields: str | None,
    ) -> Credential:
        if any(field not in SECRET_FIELDS for field in secret_fields):
            raise CredentialInvalidError("Unsupported credential field")
        credential = self._require(credential_id, for_update=True)
        values = asdict(self._decrypt(credential))
        values.update(secret_fields)
        if name is not None:
            credential.name = self._normalize_name(name)
        if allowed_domains is not None:
            credential.allowed_domains = self._normalize_domains(allowed_domains)
        if preferred_verification is not None:
            credential.preferred_verification = self._normalize_verification(preferred_verification)
        if is_default is not None:
            credential.is_default = is_default
        self._write_secret(credential, CredentialSecret(**values))
        credential.save()
        return credential

    @transaction.atomic
    def delete(self, credential_id: UUID | str) -> None:
        credential = self._require(credential_id, for_update=True)
        # Persist the wipe before deletion, including before pre_delete signals run.
        credential.ciphertext = b""
        credential.save(update_fields=["ciphertext", "updated_at"])
        credential.delete()

    def get_metadata(self, credential_id: UUID | str) -> CredentialMetadata:
        return self._metadata(self._require(credential_id))

    def require_selected(self, credential_ids: Iterable[UUID | str]) -> list[CredentialMetadata]:
        selected = []
        seen: set[UUID] = set()
        for credential_id in credential_ids:
            credential = self._require(credential_id)
            if credential.id not in seen:
                selected.append(self._metadata(credential))
                seen.add(credential.id)
        return selected

    def resolve_for_domain(self, credential_id: UUID | str, host: str) -> CredentialSecret:
        credential = self._require(credential_id)
        try:
            normalized = normalize_hostname(host)
        except ValueError:
            raise CredentialDomainDeniedError("Credential target host is invalid") from None
        if not matches_allowlist(normalized, credential.allowed_domains):
            raise CredentialDomainDeniedError("Credential is not allowed on this host")
        return self._decrypt(credential)

    @staticmethod
    def _require(credential_id: UUID | str, *, for_update: bool = False) -> Credential:
        queryset = Credential.objects.select_for_update() if for_update else Credential.objects
        try:
            return queryset.get(pk=credential_id)
        except (Credential.DoesNotExist, ValidationError, ValueError, TypeError):
            raise CredentialNotFoundError("Credential was not found") from None

    def _decrypt(self, credential: Credential) -> CredentialSecret:
        plaintext = self._cipher.decrypt(credential.ciphertext, self._associated_data(credential))
        try:
            values = json.loads(plaintext)
            if not isinstance(values, dict) or any(field not in SECRET_FIELDS for field in values):
                raise ValueError
            if any(value is not None and not isinstance(value, str) for value in values.values()):
                raise ValueError
            return CredentialSecret(**values)
        except (ValueError, TypeError):
            raise CredentialInvalidError("Saved credential payload is invalid") from None

    def _write_secret(self, credential: Credential, secret: CredentialSecret) -> None:
        values = asdict(secret)
        if any(value is not None and not isinstance(value, str) for value in values.values()):
            raise CredentialInvalidError("Credential values must be text")
        credential.available_fields = [field for field, value in values.items() if value]
        if not credential.available_fields:
            raise CredentialInvalidError("Add at least one login, password, or contact detail")
        login = secret.login or secret.email or ""
        credential.login_hint = self._mask_login(login) if login else ""
        credential.ciphertext = self._cipher.encrypt(
            json.dumps(values).encode(), self._associated_data(credential)
        )

    @staticmethod
    def _associated_data(credential: Credential) -> bytes:
        return b"credential:" + str(credential.id).encode()

    @staticmethod
    def _mask_login(login: str) -> str:
        if "@" in login:
            local, domain = login.rsplit("@", 1)
            return f"{local[:1]}***@{domain}"
        return f"{login[:1]}***"

    @staticmethod
    def _normalize_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise CredentialInvalidError("A credential name of at most 255 characters is required")
        return name.strip()

    @staticmethod
    def _normalize_domains(domains: Iterable[str]) -> list[str]:
        try:
            if isinstance(domains, str):
                raise ValueError
            normalized = list(dict.fromkeys(normalize_allowlist_entry(domain) for domain in domains))
            if not normalized:
                raise ValueError
            return normalized
        except (ValueError, TypeError, AttributeError):
            raise CredentialInvalidError("Provide valid credential domains") from None

    @staticmethod
    def _normalize_verification(methods: Iterable[str]) -> list[str]:
        if isinstance(methods, str):
            raise CredentialInvalidError("Verification preferences must be a list of text")
        values = list(methods)
        if any(not isinstance(method, str) for method in values):
            raise CredentialInvalidError("Verification preferences must be a list of text")
        return [] if "ask" in values else list(dict.fromkeys(values))

    @staticmethod
    def _metadata(credential: Credential) -> CredentialMetadata:
        return CredentialMetadata(
            id=credential.id,
            name=credential.name,
            allowed_domains=tuple(credential.allowed_domains),
            login_hint=credential.login_hint,
            is_default=credential.is_default,
            available_fields=tuple(credential.available_fields),
            preferred_verification=tuple(credential.preferred_verification),
        )
