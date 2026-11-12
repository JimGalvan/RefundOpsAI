"""The recordings shipped in demo_data/replays replay cleanly into an offline demo case."""

import pytest
from django.core.management import call_command

from cases.models import Case, Evidence, Investigation, Recommendation
from console.hub import Hub
from console.replay import fixture_for, replay_wanted, run_replay

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("order_ref", "action", "amount"),
    [("ORD-10482", "ACCEPT_CLAIM", "120.00"), ("ORD-10517", "MAKE_OFFER", "25.00")],
)
async def test_shipped_recording_replays(settings, tmp_path, monkeypatch, order_ref, action, amount) -> None:
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.delenv("REFUNDOPS_REPLAY", raising=False)
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from asgiref.sync import sync_to_async

    await sync_to_async(call_command)("seed_demo", "--offline-cases", "--reset")
    case = await Case.objects.aget(order_ref=order_ref)
    assert await sync_to_async(replay_wanted)(case)
    assert fixture_for(case)["recommendation"]["action"] == action

    hub = Hub()
    investigation_id = await run_replay(case.pk, hub, speed=10_000)

    investigation = await Investigation.objects.aget(pk=investigation_id)
    assert investigation.status == Investigation.Status.COMPLETED, investigation.error
    evidence = [item async for item in Evidence.objects.filter(investigation=investigation)]
    assert len(evidence) >= 6
    assert all((tmp_path / item.screenshot).is_file() for item in evidence if item.screenshot)
    recommendation = await Recommendation.objects.aget(investigation=investigation)
    assert recommendation.action == action
    assert str(recommendation.amount) == amount
    assert set(recommendation.citations) <= {item.pk for item in evidence}
    await case.arefresh_from_db()
    assert case.state == Case.State.READY
