import pytest


@pytest.mark.e2e
async def test_no_answer_is_needs_user_and_redelivery_does_not_create_second_call(system):
    await system.approve_demo_plan_twice_concurrently()
    await system.deliver_voice_outcome(action_id="call-dinner", outcome="NO_ANSWER")
    await system.redeliver_voice_webhook(provider_event_id="evt-1")
    await system.redeliver_voice_webhook(provider_event_id="evt-1")

    dashboard = await system.dashboard(user_id="demo")
    assert system.voice.create_call_count == 1
    assert dashboard.outcomes[0].status == "needs_user"
