"""Budget arithmetic — the part of the quota feature that must not be off by one."""

from app.services.usage_service import budget_state


def test_unlimited_budget_never_exceeds():
    state = budget_state(10_000, 0)
    assert state["budget"] == 0
    assert state["remaining"] is None
    assert state["exceeded"] is False


def test_remaining_and_exhaustion_boundary():
    assert budget_state(0, 100) == {
        "used": 0,
        "budget": 100,
        "remaining": 100,
        "exceeded": False,
    }
    assert budget_state(99, 100)["exceeded"] is False
    # Exactly at the limit counts as exhausted: the next request would overspend.
    assert budget_state(100, 100)["exceeded"] is True
    assert budget_state(120, 100)["remaining"] == 0
