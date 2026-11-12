import uuid

from django.core.exceptions import ValidationError
from django.db import models

SECRET_FIELDS = ("login", "password", "email", "first_name", "last_name", "phone")


def validate_available_fields(value: list[str]) -> None:
    if not isinstance(value, list) or any(field not in SECRET_FIELDS for field in value):
        raise ValidationError("Credential fields must be supported field names.")


class Credential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    allowed_domains = models.JSONField(default=list)
    login_hint = models.CharField(max_length=320, blank=True)
    available_fields = models.JSONField(default=list, validators=[validate_available_fields])
    preferred_verification = models.JSONField(default=list, blank=True)
    ciphertext = models.BinaryField(editable=False)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Credential {self.pk}"
