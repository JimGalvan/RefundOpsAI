from dataclasses import asdict
from typing import Any

from django import forms
from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters

from navgent.models import CredentialSecret
from vault.exceptions import CredentialError
from vault.models import SECRET_FIELDS, Credential
from vault.service import CredentialService


class CredentialAdminForm(forms.ModelForm):
    login = forms.CharField(required=False, strip=False, widget=forms.PasswordInput)
    password = forms.CharField(required=False, strip=False, widget=forms.PasswordInput)
    email = forms.CharField(required=False, strip=False, widget=forms.PasswordInput)
    first_name = forms.CharField(required=False, strip=False, widget=forms.PasswordInput)
    last_name = forms.CharField(required=False, strip=False, widget=forms.PasswordInput)
    phone = forms.CharField(required=False, strip=False, widget=forms.PasswordInput)

    class Meta:
        model = Credential
        fields = (
            "name", "allowed_domains", "is_default", "preferred_verification", *SECRET_FIELDS,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in SECRET_FIELDS:
            self.fields[field].help_text = "Write only. Leave blank to keep the saved value."
            self.fields[field].widget.attrs["autocomplete"] = "new-password"
            self.initial.pop(field, None)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        service = CredentialService()
        try:
            if "allowed_domains" in cleaned:
                cleaned["allowed_domains"] = service._normalize_domains(cleaned["allowed_domains"])
            cleaned["preferred_verification"] = service._normalize_verification(
                cleaned.get("preferred_verification") or []
            )
            if not self.instance.available_fields and not any(cleaned.get(f) for f in SECRET_FIELDS):
                raise forms.ValidationError("Add at least one login, password, or contact detail")
        except CredentialError as exception:
            raise forms.ValidationError(str(exception)) from None
        return cleaned

    def save(self, commit: bool = True) -> Credential:
        credential = super().save(commit=False)
        service = CredentialService()
        secret = service._decrypt(credential) if not credential._state.adding else CredentialSecret()
        values = asdict(secret)
        values.update({field: self.cleaned_data[field] for field in SECRET_FIELDS if self.cleaned_data[field]})
        service._write_secret(credential, CredentialSecret(**values))
        if commit:
            credential.save()
            self.save_m2m()
        return credential


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    form = CredentialAdminForm
    list_display = ("name", "allowed_domains", "login_hint", "available_fields")
    readonly_fields = ("login_hint", "available_fields", "created_at", "updated_at")
    fields = (
        "name", "allowed_domains", "login_hint", "available_fields", *SECRET_FIELDS,
        "preferred_verification", "is_default", "created_at", "updated_at",
    )

    @method_decorator(sensitive_post_parameters(*SECRET_FIELDS))
    def add_view(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().add_view(request, *args, **kwargs)

    @method_decorator(sensitive_post_parameters(*SECRET_FIELDS))
    def change_view(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().change_view(request, *args, **kwargs)

    def delete_model(self, request: HttpRequest, obj: Credential) -> None:
        CredentialService().delete(obj.pk)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Credential]) -> None:
        service = CredentialService()
        for credential_id in queryset.values_list("pk", flat=True):
            service.delete(credential_id)
