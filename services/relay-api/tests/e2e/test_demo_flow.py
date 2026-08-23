import pytest


@pytest.mark.e2e
async def test_delay_to_verified_outcomes_with_one_call(system):
    await system.deliver_gmail_delay(message_id="m1", history_id="900")
    dashboard = await system.dashboard(user_id="demo")
    assert dashboard.approval is not None
    assert dashboard.approval.state == "awaiting_approval"

    await system.approve_once(
        approval_id=dashboard.approval.approval_id,
        version=dashboard.approval.version,
    )
    await system.deliver_voice_outcome(action_id="call-dinner", outcome="CONFIRMED_PERMITTED")
    await system.deliver_calendar_readback(
        action_id="calendar-hold", exists=True, visibility="private"
    )

    outcome_states = {
        item.action_id: item.status for item in (await system.dashboard(user_id="demo")).outcomes
    }
    assert outcome_states["call-dinner"] == "verified"
    assert outcome_states["calendar-hold"] == "verified"
    assert system.voice.create_call_count == 1
