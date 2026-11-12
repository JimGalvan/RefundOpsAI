import base64

import pytest

from vault.service import CredentialService


@pytest.fixture(autouse=True)
def credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFUNDOPS_CREDENTIAL_KEY", base64.b64encode(bytes(range(32))).decode())


@pytest.fixture
def service() -> CredentialService:
    return CredentialService()
