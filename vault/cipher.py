import base64
import binascii
import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vault.exceptions import CredentialConfigurationError

KEY_LENGTH_BYTES = 32
NONCE_LENGTH_BYTES = 12
CURRENT_KEY_ID = b"v1"


class CredentialCipher(Protocol):
    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes: ...


class LocalAesGcmCredentialCipher:
    def __init__(self) -> None:
        self._cipher: AESGCM | None = None

    def _configured_cipher(self) -> AESGCM:
        if self._cipher is None:
            encoded = os.environ.get("REFUNDOPS_CREDENTIAL_KEY", "")
            try:
                key = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise CredentialConfigurationError(
                    "REFUNDOPS_CREDENTIAL_KEY must be base64 of 32 bytes"
                ) from None
            if len(key) != KEY_LENGTH_BYTES:
                raise CredentialConfigurationError(
                    "REFUNDOPS_CREDENTIAL_KEY must be base64 of 32 bytes"
                )
            self._cipher = AESGCM(key)
        return self._cipher

    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        cipher = self._configured_cipher()
        nonce = os.urandom(NONCE_LENGTH_BYTES)
        return CURRENT_KEY_ID + b":" + nonce + cipher.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes:
        cipher = self._configured_cipher()
        key_id, separator, body = bytes(ciphertext).partition(b":")
        if not separator or key_id != CURRENT_KEY_ID or len(body) < NONCE_LENGTH_BYTES + 16:
            raise CredentialConfigurationError("Saved credential payload is unreadable")
        try:
            return cipher.decrypt(body[:NONCE_LENGTH_BYTES], body[NONCE_LENGTH_BYTES:], associated_data)
        except (InvalidTag, ValueError):
            raise CredentialConfigurationError("Saved credential could not be decrypted") from None
