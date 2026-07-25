from datetime import date, datetime, timezone

from tools import ensure_money_tick_cache


def test_money_windows_include_full_unexecuted_provider_day():
    day = date(2026, 7, 23)

    windows = ensure_money_tick_cache.required_money_day_windows(
        [],
        additional_required_days=[day],
    )

    assert windows == {
        day: (
            datetime(2026, 7, 23, tzinfo=timezone.utc),
            datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
    }


def test_valid_but_short_money_cache_day_is_incomplete(tmp_path, monkeypatch):
    day = date(2026, 7, 21)
    (tmp_path / "2026-07-21.parquet").touch()
    incomplete_contract = {
        "coverage": {
            "complete_from_utc": "2026-07-21T00:00:00+00:00",
            "complete_through_utc": "2026-07-21T12:00:00+00:00",
        }
    }
    monkeypatch.setattr(
        ensure_money_tick_cache.base,
        "load_valid_day_contract",
        lambda *args, **kwargs: incomplete_contract,
    )

    status = ensure_money_tick_cache._classify_cache_days(
        {
            day: (
                datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
                datetime(2026, 7, 21, 16, tzinfo=timezone.utc),
            )
        },
        cache_dir=tmp_path,
        symbol="EURUSD",
    )

    assert status["cached"] == []
    assert status["missing"] == []
    assert status["invalid"] == []
    assert status["incomplete"] == [day]
    assert status["refresh"] == [day]

