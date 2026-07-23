from entry_execution_gate import EntryExecutionGate


def test_claim_rejects_concurrent_and_committed_delivery():
    gate = EntryExecutionGate(max_committed=10)

    assert gate.claim("canal2", 266) is True
    assert gate.claim("canal2", 266) is False

    gate.commit("canal2", 266)
    gate.release("canal2", 266)

    assert gate.claim("canal2", 266) is False
    assert gate.committed("canal2", 266) is True


def test_release_allows_retry_only_before_exposure_is_committed():
    gate = EntryExecutionGate(max_committed=10)

    assert gate.claim("canal2", 380) is True
    gate.release("canal2", 380)

    assert gate.claim("canal2", 380) is True


def test_claim_identity_is_scoped_by_channel():
    gate = EntryExecutionGate(max_committed=10)

    assert gate.claim("canal1", 266) is True
    assert gate.claim("canal2", 266) is True


def test_committed_claims_are_bounded_without_evicting_opening_claims():
    gate = EntryExecutionGate(max_committed=2)
    assert gate.claim("canal2", 1) is True
    assert gate.claim("canal2", 2) is True
    assert gate.claim("canal2", 3) is True

    gate.commit("canal2", 1)
    gate.commit("canal2", 2)
    gate.commit("canal2", 3)

    assert gate.committed("canal2", 1) is False
    assert gate.committed("canal2", 2) is True
    assert gate.committed("canal2", 3) is True
