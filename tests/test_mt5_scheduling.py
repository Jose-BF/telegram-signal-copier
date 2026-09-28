import itertools

from mt5_scheduling import admission_available, next_transport_kind


def test_class_selection_equals_existing_runtime_predicates_exhaustively():
    for management, trade, read, streak, maximum in itertools.product(range(4), range(4), range(4), range(7), (1, 4, 6)):
        waiters = dict(management=management, trade=trade, read=read)
        allowed = []
        for kind, count in waiters.items():
            trade_turn = ((kind == "management" or kind == "trade" and management == 0)
                          and (read == 0 or streak < maximum))
            read_turn = kind == "read" and (trade + management == 0 or streak >= maximum)
            if count and (trade_turn or read_turn):
                allowed.append(kind)
        assert allowed == ([] if not any(waiters.values()) else [next_transport_kind(waiters, streak, maximum)])


def test_one_extra_slot_for_management_not_entries_or_reads():
    for capacity in (1, 2, 8, 128):
        for pending in range(capacity + 3):
            assert admission_available(pending, capacity, management=False) == (pending < capacity)
            assert admission_available(pending, capacity, management=True) == (pending < capacity + 1)
