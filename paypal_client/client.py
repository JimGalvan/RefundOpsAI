"""Sandbox-only REST calls, OAuth caching, and bounded idempotent retries."""

import base64
import json
import logging
import os
import threading
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from .errors import PayPalError, safe_text

logger = logging.getLogger("paypal_client")
BASE_URL = "https://api-m.sandbox.paypal.com"
Amount = Decimal | str
JSON = dict[str, Any]


def money(amount: Amount, currency: str) -> dict[str, str]:
    if not isinstance(amount, (Decimal, str)):
        raise TypeError("amount must be a Decimal or string")
    try:
        value = Decimal(amount)
        if not value.is_finite():
            raise ValueError("amount must be finite")
        value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("amount must be a finite decimal") from exc
    return {"currency_code": currency, "value": format(value, ".2f")}


def without_none(values: JSON) -> JSON:
    return {key: value for key, value in values.items() if value is not None}


def build_order_payload(
    *,
    amount: Amount,
    invoice_id: str,
    currency: str = "USD",
    custom_id: str | None = None,
    description: str | None = None,
    shipping: dict[str, str] | None = None,
    return_url: str = "https://example.com/return",
    cancel_url: str = "https://example.com/cancel",
) -> JSON:
    """Build the same payload for API calls and credential-free seed previews."""
    unit = without_none(
        {
            "reference_id": invoice_id,
            "invoice_id": invoice_id,
            "custom_id": custom_id if custom_id is not None else invoice_id,
            "description": description,
            "amount": money(amount, currency),
        }
    )
    if shipping is not None:
        fields = {
            "address_line_1": "line1",
            "address_line_2": "line2",
            "admin_area_2": "city",
            "admin_area_1": "state",
            "postal_code": "postal_code",
            "country_code": "country_code",
        }
        unit["shipping"] = {
            "type": "SHIPPING",
            "name": without_none({"full_name": shipping.get("name")}),
            "address": without_none(
                {key: shipping.get(source) for key, source in fields.items()}
            ),
        }
    return {
        "intent": "CAPTURE",
        "purchase_units": [unit],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                    "shipping_preference": "SET_PROVIDED_ADDRESS"
                    if shipping is not None
                    else "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        env: str = "sandbox",
        client: httpx.Client | None = None,
    ) -> None:
        if env != "sandbox":
            raise ValueError("Only the PayPal sandbox is supported")
        if not client_id or not client_secret:
            raise ValueError("PayPal client id and secret are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client if client is not None else httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "PayPalClient":
        return cls(
            os.environ.get("PAYPAL_CLIENT_ID", ""),
            os.environ.get("PAYPAL_CLIENT_SECRET", ""),
            env=os.environ.get("PAYPAL_ENV", "sandbox"),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "PayPalClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _secrets(self) -> tuple[str, ...]:
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        return self._client_id, self._client_secret, self._token or "", basic

    def _send(
        self,
        method: str,
        path: str,
        *,
        max_retries: int = 2,
        **kwargs: Any,
    ) -> tuple[httpx.Response, int]:
        headers = kwargs.get("headers", {})
        retries = max_retries if headers.get("PayPal-Request-Id") else 0
        kwargs.setdefault("auth", None)
        for attempt in range(retries + 1):
            response = self._client.request(
                method, BASE_URL + path, follow_redirects=False, **kwargs
            )
            debug_id = self._debug_id(response)
            logger.info(
                "%s %s status=%s debug_id=%s",
                method,
                path,
                response.status_code,
                safe_text(debug_id or "-", self._secrets()),
            )
            if response.status_code != 429 and not 500 <= response.status_code <= 599:
                return response, attempt
            if attempt < retries:
                time.sleep(0.1 * (2**attempt))
        return response, retries

    @staticmethod
    def _debug_id(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return (
            payload.get("debug_id") if isinstance(payload, dict) else None
        ) or response.headers.get("PayPal-Debug-Id")

    def _parse(self, response: httpx.Response) -> JSON:
        try:
            raw = response.json() if response.content else {}
        except ValueError:
            raw = response.text
        if not response.is_success:
            payload = raw if isinstance(raw, dict) else {}
            raise PayPalError(
                response.status_code,
                payload.get("name") or payload.get("error") or "HTTP_ERROR",
                payload.get("message")
                or payload.get("error_description")
                or response.reason_phrase,
                self._debug_id(response),
                payload.get("details"),
                raw,
                secrets=self._secrets(),
            )
        if not isinstance(raw, dict):
            raise ValueError("Expected a JSON object from PayPal")
        return raw

    def access_token(self) -> str:
        return self._access_token()

    def _access_token(self, rejected_token: str | None = None) -> str:
        with self._token_lock:
            if (
                self._token
                and time.monotonic() < self._expires_at
                and self._token != rejected_token
            ):
                return self._token
            response, _ = self._send(
                "POST",
                "/v1/oauth2/token",
                auth=httpx.BasicAuth(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
                headers={
                    "PayPal-Request-Id": uuid4().hex,
                    "Accept": "application/json",
                },
            )
            payload = self._parse(response)
            self._token = payload["access_token"]
            self._expires_at = time.monotonic() + max(
                0.0, float(payload["expires_in"]) - 60.0
            )
            return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> JSON:
        headers = {"Accept": "application/json"}
        if method == "POST":
            headers["PayPal-Request-Id"] = request_id or uuid4().hex
        token = self.access_token()
        headers["Authorization"] = f"Bearer {token}"
        response, retries_used = self._send(method, path, headers=headers, **kwargs)
        if response.status_code == 401:
            token = self._access_token(rejected_token=token)
            headers["Authorization"] = f"Bearer {token}"
            response, _ = self._send(
                method, path, max_retries=2 - retries_used, headers=headers, **kwargs
            )
        return self._parse(response)

    def create_order(
        self,
        *,
        amount: Amount,
        invoice_id: str,
        currency: str = "USD",
        custom_id: str | None = None,
        description: str | None = None,
        shipping: dict[str, str] | None = None,
        request_id: str | None = None,
        return_url: str = "https://example.com/return",
        cancel_url: str = "https://example.com/cancel",
    ) -> JSON:
        payload = build_order_payload(
            amount=amount,
            invoice_id=invoice_id,
            currency=currency,
            custom_id=custom_id,
            description=description,
            shipping=shipping,
            return_url=return_url,
            cancel_url=cancel_url,
        )
        return self._request(
            "POST", "/v2/checkout/orders", json=payload, request_id=request_id
        )

    @staticmethod
    def approval_url(order: JSON) -> str:
        for rel in ("payer-action", "approve"):
            for link in order.get("links", []):
                if link.get("rel") == rel and link.get("href"):
                    return link["href"]
        raise ValueError("Order has no payer-action or approve link")

    def get_order(self, order_id: str) -> JSON:
        return self._request("GET", f"/v2/checkout/orders/{quote(order_id, safe='')}")

    def capture_order(self, order_id: str, *, request_id: str | None = None) -> JSON:
        return self._request(
            "POST",
            f"/v2/checkout/orders/{quote(order_id, safe='')}/capture",
            json={},
            request_id=request_id,
        )

    def get_capture(self, capture_id: str) -> JSON:
        return self._request(
            "GET", f"/v2/payments/captures/{quote(capture_id, safe='')}"
        )

    def refund_capture(
        self,
        capture_id: str,
        *,
        amount: Amount | None = None,
        currency: str = "USD",
        note_to_payer: str | None = None,
        invoice_id: str | None = None,
        request_id: str | None = None,
    ) -> JSON:
        payload = without_none(
            {
                "amount": money(amount, currency) if amount is not None else None,
                "note_to_payer": note_to_payer,
                "invoice_id": invoice_id,
            }
        )
        return self._request(
            "POST",
            f"/v2/payments/captures/{quote(capture_id, safe='')}/refund",
            json=payload,
            request_id=request_id,
        )

    def get_refund(self, refund_id: str) -> JSON:
        return self._request("GET", f"/v2/payments/refunds/{quote(refund_id, safe='')}")

    @staticmethod
    def extract_order_id(capture: JSON) -> str | None:
        return ((capture.get("supplementary_data") or {}).get("related_ids") or {}).get(
            "order_id"
        )

    def list_disputes(
        self,
        *,
        dispute_state: str | None = None,
        start_time: str | None = None,
        disputed_transaction_id: str | None = None,
        page_size: int = 50,
    ) -> list[JSON]:
        params = without_none(
            {
                "dispute_state": dispute_state,
                "start_time": start_time,
                "disputed_transaction_id": disputed_transaction_id,
                "page_size": page_size,
            }
        )
        return self._request("GET", "/v1/customer/disputes", params=params).get(
            "items", []
        )

    def get_dispute(self, dispute_id: str) -> JSON:
        return self._request("GET", self._dispute_path(dispute_id))

    @staticmethod
    def _dispute_path(dispute_id: str, action: str = "") -> str:
        path = f"/v1/customer/disputes/{quote(dispute_id, safe='')}"
        return f"{path}/{action}" if action else path

    def accept_claim(
        self,
        dispute_id: str,
        *,
        note: str,
        accept_claim_reason: str = "REASON_NOT_SET",
        accept_claim_type: str = "REFUND",
        refund_amount: Amount | None = None,
        currency: str = "USD",
        invoice_id: str | None = None,
        request_id: str | None = None,
    ) -> JSON:
        payload = without_none(
            {
                "note": note,
                "accept_claim_reason": accept_claim_reason,
                "accept_claim_type": accept_claim_type,
                "invoice_id": invoice_id,
                "refund_amount": money(refund_amount, currency)
                if refund_amount is not None
                else None,
            }
        )
        return self._request(
            "POST",
            self._dispute_path(dispute_id, "accept-claim"),
            json=payload,
            request_id=request_id,
        )

    def make_offer(
        self,
        dispute_id: str,
        *,
        note: str,
        amount: Amount,
        offer_type: str = "REFUND",
        currency: str = "USD",
        invoice_id: str | None = None,
        request_id: str | None = None,
    ) -> JSON:
        payload = without_none(
            {
                "note": note,
                "offer_amount": money(amount, currency),
                "offer_type": offer_type,
                "invoice_id": invoice_id,
            }
        )
        return self._request(
            "POST",
            self._dispute_path(dispute_id, "make-offer"),
            json=payload,
            request_id=request_id,
        )

    def provide_evidence(
        self,
        dispute_id: str,
        *,
        evidences: list[JSON],
        note: str | None = None,
        request_id: str | None = None,
    ) -> JSON:
        payload = without_none({"evidences": evidences, "note": note})
        return self._request(
            "POST",
            self._dispute_path(dispute_id, "provide-evidence"),
            files={"input": (None, json.dumps(payload), "application/json")},
            request_id=request_id,
        )

    def send_message(
        self, dispute_id: str, message: str, *, request_id: str | None = None
    ) -> JSON:
        return self._request(
            "POST",
            self._dispute_path(dispute_id, "send-message"),
            json={"message": message},
            request_id=request_id,
        )

    def sandbox_require_evidence(
        self, dispute_id: str, action: str = "SELLER_EVIDENCE"
    ) -> JSON:
        return self._request(
            "POST",
            self._dispute_path(dispute_id, "require-evidence"),
            json={"action": action},
        )

    def sandbox_adjudicate(self, dispute_id: str, outcome: str = "BUYER_FAVOR") -> JSON:
        return self._request(
            "POST",
            self._dispute_path(dispute_id, "adjudicate"),
            json={"adjudication_outcome": outcome},
        )

    def verify_webhook_signature(
        self,
        *,
        headers: dict[str, str],
        body: str | bytes,
        webhook_id: str,
    ) -> bool:
        normalized = {key.lower(): value for key, value in headers.items()}
        payload = {
            field: normalized[f"paypal-{field.replace('_', '-')}"]
            for field in (
                "auth_algo",
                "cert_url",
                "transmission_id",
                "transmission_sig",
                "transmission_time",
            )
        }
        payload.update(webhook_id=webhook_id, webhook_event=json.loads(body))
        result = self._request(
            "POST", "/v1/notifications/verify-webhook-signature", json=payload
        )
        return result.get("verification_status") == "SUCCESS"
