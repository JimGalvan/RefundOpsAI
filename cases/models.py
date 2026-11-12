"""Case, investigation, evidence, recommendation, decision and PayPal action models."""

import uuid

from django.db import models


class Case(models.Model):
    class Source(models.TextChoices):
        DISPUTE = "dispute", "PayPal dispute"
        COMPLAINT = "complaint", "Customer complaint"

    class State(models.TextChoices):
        NEW = "new", "New"
        INVESTIGATING = "investigating", "Investigating"
        READY = "ready", "Ready for review"
        APPROVED = "approved", "Approved"
        EXECUTING = "executing", "Executing"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected"
        NEEDS_INFO = "needs_info", "Needs more information"
        FAILED = "failed", "Failed"

    source = models.CharField(max_length=16, choices=Source, default=Source.DISPUTE)
    state = models.CharField(max_length=16, choices=State, default=State.NEW, db_index=True)

    paypal_dispute_id = models.CharField(max_length=64, blank=True, db_index=True)
    paypal_capture_id = models.CharField(max_length=64, blank=True)
    paypal_order_id = models.CharField(max_length=64, blank=True)
    order_ref = models.CharField(max_length=64, blank=True, help_text="The store's order number (PayPal invoice_id).")

    buyer_name = models.CharField(max_length=200, blank=True)
    buyer_email = models.EmailField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    reason = models.CharField(max_length=64, blank=True)
    stage = models.CharField(max_length=32, blank=True)
    dispute_status = models.CharField(max_length=32, blank=True)
    response_due_at = models.DateTimeField(null=True, blank=True)
    allowed_actions = models.JSONField(default=list, blank=True)
    paypal_snapshot = models.JSONField(default=dict, blank=True, help_text="Latest dispute details from PayPal.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Case {self.pk} {self.order_ref or self.paypal_dispute_id} ({self.state})"


class Investigation(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="investigations")
    status = models.CharField(max_length=16, choices=Status, default=Status.RUNNING)
    strong_model = models.CharField(max_length=100, blank=True)
    fast_model = models.CharField(max_length=100, blank=True)
    summary = models.TextField(blank=True)
    open_questions = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    usage = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class AgentEvent(models.Model):
    """One line of the investigation log, replayed to the live view and kept for the audit trail."""

    investigation = models.ForeignKey(Investigation, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["investigation_id", "seq"]
        constraints = [models.UniqueConstraint(fields=["investigation", "seq"], name="unique_event_seq")]


class Evidence(models.Model):
    """A fact the agent recorded. The quote was verified against the page or PayPal record it came from."""

    class Kind(models.TextChoices):
        PAYMENT = "payment", "Payment"
        DISPUTE = "dispute", "Dispute"
        FULFILLMENT = "fulfillment", "Fulfillment"
        TRACKING = "tracking", "Tracking"
        POLICY = "policy", "Policy"
        MESSAGE = "message", "Customer message"
        OTHER = "other", "Other"

    class Supports(models.TextChoices):
        BUYER = "buyer", "Supports the buyer"
        MERCHANT = "merchant", "Supports the merchant"
        NEUTRAL = "neutral", "Neutral"
        UNTRUSTED = "untrusted", "Untrusted content"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="evidence")
    investigation = models.ForeignKey(Investigation, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.OTHER)
    supports = models.CharField(max_length=16, choices=Supports, default=Supports.NEUTRAL)
    claim = models.TextField()
    quote = models.TextField(help_text="Verbatim text from the source.")
    source_label = models.CharField(max_length=100, blank=True)
    source_url = models.CharField(max_length=500, blank=True)
    screenshot = models.CharField(max_length=300, blank=True, help_text="Path under MEDIA_ROOT.")
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class Recommendation(models.Model):
    class Action(models.TextChoices):
        ACCEPT_CLAIM = "ACCEPT_CLAIM", "Accept the claim (refund)"
        MAKE_OFFER = "MAKE_OFFER", "Make an offer"
        PROVIDE_EVIDENCE = "PROVIDE_EVIDENCE", "Provide evidence"
        REFUND = "REFUND", "Refund the payment"
        NEEDS_INFO = "NEEDS_INFO", "Needs more information"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="recommendations")
    investigation = models.ForeignKey(Investigation, on_delete=models.SET_NULL, null=True, related_name="recommendations")
    action = models.CharField(max_length=24, choices=Action)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    headline = models.CharField(max_length=200)
    rationale = models.TextField()
    buyer_note = models.TextField(blank=True)
    citations = models.JSONField(default=list, blank=True, help_text="Evidence ids backing the rationale.")
    contradictions = models.JSONField(default=list, blank=True)
    missing = models.JSONField(default=list, blank=True)
    alternatives = models.JSONField(default=list, blank=True)
    confidence = models.CharField(max_length=8, blank=True)
    guardrails = models.JSONField(default=dict, blank=True)
    model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Decision(models.Model):
    class Outcome(models.TextChoices):
        APPROVED = "approved", "Approved"
        EDITED = "edited", "Approved with edits"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="decisions")
    recommendation = models.ForeignKey(Recommendation, on_delete=models.CASCADE, related_name="decisions")
    outcome = models.CharField(max_length=16, choices=Outcome)
    action = models.CharField(max_length=24, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.TextField(blank=True, help_text="Message to the buyer, or the reason for rejecting.")
    decided_by = models.CharField(max_length=100, default="merchant")
    decided_at = models.DateTimeField(auto_now_add=True)


class PayPalAction(models.Model):
    """One call PayPal was asked to carry out, with the evidence of what it answered."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "Succeeded"
        FAILED = "failed", "Failed"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="paypal_actions")
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE, related_name="paypal_actions")
    kind = models.CharField(max_length=24)
    idempotency_key = models.CharField(max_length=64, unique=True)
    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status, default=Status.PENDING)
    paypal_ids = models.JSONField(default=dict, blank=True, help_text="Refund id, dispute id and other ids PayPal returned.")
    verification = models.JSONField(default=dict, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
