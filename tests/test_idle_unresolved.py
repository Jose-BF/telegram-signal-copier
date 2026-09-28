from datetime import datetime, timezone

from research.causal_canal1_stream import CausalUnresolvedMessage
from research.causal_text_admission import CausalRouteSession


def _unresolved():
    return CausalUnresolvedMessage(message_id=23073, message_revision_id="msgrev_x",
                                   observed_at=datetime(2026, 9, 25, 10, 23, 32, tzinfo=timezone.utc),
                                   reason="conditional_management_unsupported")


def _session(**kwargs):
    return CausalRouteSession(replay_entry=lambda signal: None, initial_universe_complete=True, **kwargs)


def test_default_blocks_universe_on_unresolved_text():
    session = _session()
    decision = session.step("unresolved", _unresolved())
    assert decision["status"] == "blocked_unresolved_provider_event"
    assert session.report()["prior_universe_complete"] is False


def test_opt_in_ignores_unresolved_text_when_nothing_is_open():
    session = _session(idle_unresolved_ignored=True)
    decision = session.step("unresolved", _unresolved())
    assert decision["status"] == "unresolved_ignored_no_open_signal"
    assert session.report()["prior_universe_complete"] is True
