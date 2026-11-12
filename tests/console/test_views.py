from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from cases.models import (
    AgentEvent,
    Case,
    Decision,
    Evidence,
    Investigation,
    Recommendation,
)
from console.demo import seed_offline_cases
from console.hub import hub
from console.presenters import describe_event, due_info, link_citations


@pytest.fixture
def case(db):
    return seed_offline_cases()[0]


@pytest.fixture
def ready_case(case):
    investigation = Investigation.objects.create(
        case=case, status=Investigation.Status.COMPLETED, summary="The parcel came back.", finished_at=timezone.now()
    )
    carrier = Evidence.objects.create(
        case=case, investigation=investigation, kind="tracking", supports="buyer",
        claim="The carrier returned the parcel.", quote="Returned to sender",
        source_label="Meridian Parcel", source_url="http://tracking.localhost:8000/track/x/",
    )
    policy = Evidence.objects.create(
        case=case, investigation=investigation, kind="policy", supports="buyer",
        claim="Policy refunds returned orders.", quote="we refund the full order total", source_label="Harbor & Pine",
    )
    Recommendation.objects.create(
        case=case, investigation=investigation, action="ACCEPT_CLAIM", amount=Decimal("120.00"), currency="USD",
        headline="Refund in full", rationale=f"Returned (E{carrier.pk}) and policy (E{policy.pk}).",
        buyer_note="We are refunding you.", citations=[carrier.pk, policy.pk],
        guardrails={"ok": True, "violations": []}, confidence="high",
    )
    Case.objects.filter(pk=case.pk).update(state=Case.State.READY)
    case.refresh_from_db()
    return case


def test_queue_lists_cases_with_their_state(case):
    page = Client().get("/").content.decode()
    assert case.order_ref in page
    assert "Not investigated" in page
    assert "offline demo" in page


def test_new_case_offers_to_investigate(case):
    page = Client().get(f"/cases/{case.pk}/").content.decode()
    assert "Investigate this case" in page
    assert "Approve and" not in page


def test_ready_case_shows_evidence_copies_and_the_exact_approve_action(ready_case):
    page = Client().get(f"/cases/{ready_case.pk}/").content.decode()
    assert "Approve and refund 120.00 USD" in page
    assert "tint-carrier" in page and "tint-policy" in page
    assert 'href="#e' in page  # citation keys link to the evidence they cite
    assert "Reject this recommendation" in page


def test_approving_an_offline_case_records_the_decision_and_sends_nothing(ready_case):
    response = Client().post(f"/cases/{ready_case.pk}/decide/", {"outcome": "approve"})
    assert "nothing was sent to PayPal" in response.content.decode()
    ready_case.refresh_from_db()
    assert ready_case.state == Case.State.APPROVED
    assert Decision.objects.get(case=ready_case).outcome == Decision.Outcome.APPROVED
    assert ready_case.paypal_actions.count() == 0


def test_rejecting_needs_a_reason(ready_case):
    response = Client().post(f"/cases/{ready_case.pk}/decide/", {"outcome": "reject", "note": " "})
    assert "requires a reason" in response.content.decode()
    ready_case.refresh_from_db()
    assert ready_case.state == Case.State.READY


def test_rejecting_with_a_reason_is_recorded(ready_case):
    response = Client().post(f"/cases/{ready_case.pk}/decide/", {"outcome": "reject", "note": "Buyer already refunded."})
    assert "Rejected" in response.content.decode()
    ready_case.refresh_from_db()
    assert ready_case.state == Case.State.REJECTED


def test_deciding_on_a_case_that_is_not_ready_is_refused(case):
    Recommendation.objects.create(
        case=case, action="NEEDS_INFO", headline="x", rationale="y", confidence="low", guardrails={"ok": True}
    )
    response = Client().post(f"/cases/{case.pk}/decide/", {"outcome": "approve"})
    assert "must be ready for review" in response.content.decode()
    case.refresh_from_db()
    assert case.state == Case.State.NEW


def test_edit_must_change_something(ready_case):
    recommendation = ready_case.recommendations.get()
    response = Client().post(
        f"/cases/{ready_case.pk}/decide/",
        {"outcome": "edit", "action": "ACCEPT_CLAIM", "amount": "120.00", "note": recommendation.buyer_note},
    )
    assert "must change the action, amount or note" in response.content.decode()


def test_feed_keeps_polling_while_running_and_refreshes_when_done(case):
    investigation = Investigation.objects.create(case=case)
    AgentEvent.objects.create(
        investigation=investigation, seq=1, type="browser.navigation.started",
        payload={"url": "http://portal.localhost:8000/orders/"},
    )
    running = Client().get(f"/cases/{case.pk}/feed/?inv={investigation.pk}&after=0&ev=0")
    assert "Opening portal.localhost:8000/orders/" in running.content.decode()
    assert 'id="poller"' in running.content.decode()
    assert "HX-Trigger" not in running

    Investigation.objects.filter(pk=investigation.pk).update(status=Investigation.Status.COMPLETED)
    done = Client().get(f"/cases/{case.pk}/feed/?inv={investigation.pk}&after=1&ev=0")
    assert 'id="poller"' not in done.content.decode()
    assert done["HX-Trigger"] == "refreshCase"


def test_feed_stays_open_while_the_recommendation_is_still_being_written(case):
    investigation = Investigation.objects.create(case=case, status=Investigation.Status.COMPLETED)
    hub.running.add(case.pk)
    try:
        response = Client().get(f"/cases/{case.pk}/feed/?inv={investigation.pk}&after=0&ev=0")
    finally:
        hub.running.discard(case.pk)
    assert 'id="poller"' in response.content.decode()
    assert "HX-Trigger" not in response


def test_frame_is_empty_until_the_agent_has_one(case):
    assert Client().get(f"/cases/{case.pk}/frame.jpg").status_code == 204
    hub.frames[case.pk] = b"\xff\xd8jpeg"
    try:
        response = Client().get(f"/cases/{case.pk}/frame.jpg")
    finally:
        hub.frames.pop(case.pk, None)
    assert response.status_code == 200 and response["Content-Type"] == "image/jpeg"


def test_citations_link_only_known_evidence_and_text_is_escaped():
    html = link_citations("Seen in E21 and E99 <b>bold</b>", {21})
    assert '<a class="cite" href="#e21">E21</a>' in html
    assert "E99" in html and 'href="#e99"' not in html
    assert "&lt;b&gt;" in html


def test_event_descriptions_hide_noise_and_never_show_secrets():
    def event(kind, payload):
        return AgentEvent(type=kind, payload=payload)

    assert describe_event(event("browser.navigation.started", {"url": "http://inbox.localhost:8000/inbox/"}))["text"] == (
        "Opening inbox.localhost:8000/inbox/"
    )
    signing_in = describe_event(event("browser.action.started", {"action": "fill_credential", "target": "Password"}))
    assert "never sees the password" in signing_in["text"]
    blocked = describe_event(event("sandbox.request_blocked", {"host": "evil.example"}))
    assert blocked["class"] == "blocked" and "evil.example" in blocked["text"]
    assert describe_event(event("run.status", {"status": "running"})) is None


def test_due_info_flags_urgent_deadlines(case):
    case.response_due_at = timezone.now() + timezone.timedelta(hours=5)
    assert due_info(case)["urgent"] is True
    case.response_due_at = timezone.now() + timezone.timedelta(days=9)
    assert due_info(case)["urgent"] is False
