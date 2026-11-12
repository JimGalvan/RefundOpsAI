"""Verified PayPal dispute notifications."""

import json
import logging
import os

import httpx
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cases import sync
from paypal_client import PayPalClient, PayPalError

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024
EVENT_TIMEOUT = 24 * 60 * 60
DISPUTE_EVENTS = {
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
}


@csrf_exempt
@require_POST
def paypal_webhook(request: HttpRequest) -> HttpResponse:
    try:
        raw_body = request.body
    except RequestDataTooBig:
        return JsonResponse({"error": "body too large"}, status=413)
    if len(raw_body) > MAX_BODY_BYTES:
        return JsonResponse({"error": "body too large"}, status=413)

    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID", "").strip()
    if not webhook_id:
        return JsonResponse({"error": "webhook not configured"}, status=503)

    try:
        client = PayPalClient.from_env()
    except ValueError:  # the REST keys are not set
        return JsonResponse({"error": "paypal not configured"}, status=503)
    try:
        try:
            verified = client.verify_webhook_signature(
                headers=request.headers, body=raw_body, webhook_id=webhook_id
            )
        except (PayPalError, httpx.HTTPError, KeyError, ValueError):
            verified = False
        if not verified:
            return JsonResponse({"error": "invalid signature"}, status=400)
        return _process_event(client, raw_body)
    finally:
        client.close()


def _process_event(client: PayPalClient, raw_body: bytes) -> JsonResponse:
    try:
        event = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid event"}, status=400)
    if not isinstance(event, dict):
        return JsonResponse({"error": "invalid event"}, status=400)

    event_type = event.get("event_type")
    event_id = event.get("id")
    if not isinstance(event_type, str) or not isinstance(event_id, str) or not event_id:
        return JsonResponse({"error": "invalid event"}, status=400)

    key = f"paypal-webhook:{event_id}"
    if cache.get(key):
        return JsonResponse({"status": "duplicate"})
    if event_type not in DISPUTE_EVENTS:
        logger.info("event_type=%r event_id=%r", event_type, event_id)
        cache.set(key, True, timeout=EVENT_TIMEOUT)
        return JsonResponse({"status": "ignored"})

    resource = event.get("resource")
    dispute_id = resource.get("dispute_id") if isinstance(resource, dict) else None
    if not isinstance(dispute_id, str) or not dispute_id.strip():
        return JsonResponse({"error": "invalid event"}, status=400)

    logger.info(
        "event_type=%r event_id=%r dispute_id=%r", event_type, event_id, dispute_id
    )
    try:
        case, _ = sync.sync_one(client, dispute_id)
    except (PayPalError, httpx.HTTPError, ValueError):  # PayPal retries a failed delivery
        logger.warning("sync failed event_id=%r dispute_id=%r", event_id, dispute_id)
        return JsonResponse({"error": "sync failed"}, status=502)
    cache.set(key, True, timeout=EVENT_TIMEOUT)
    return JsonResponse({"status": "ok", "case": case.pk})
