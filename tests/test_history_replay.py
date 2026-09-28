import json
from datetime import datetime, timezone
from types import SimpleNamespace

from research.history_replay import History, Trigger, load_triggers, server_offset_s, simulate_day


def test_broker_clock_follows_us_dst():
    assert server_offset_s(datetime(2026, 1, 14, 12, tzinfo=timezone.utc)) == 7200
    assert server_offset_s(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc)) == 7200
    assert server_offset_s(datetime(2026, 3, 9, 12, tzinfo=timezone.utc)) == 10800
    assert server_offset_s(datetime(2026, 9, 21, 12, tzinfo=timezone.utc)) == 10800


def test_gold_double_posts_are_merged(tmp_path):
    dubai = tmp_path / "d.jsonl"
    dubai.write_text(json.dumps({"chat_id": 1, "message_id": 1, "direction": "BUY",
                                 "published_utc": "2026-05-18T06:00:00Z", "source": "x"}) + "\n")
    gold = tmp_path / "g.jsonl"
    rows = [(1107, "BUY", "06:06:22"), (1110, "BUY", "06:06:26"), (1111, "SELL", "06:06:27"), (1200, "BUY", "06:10:00")]
    gold.write_text("".join(json.dumps({"chat_id": 2, "message_id": m, "direction": d, "channel": "c",
                                        "published_utc": f"2026-05-18T{t}+00:00"}) + "\n" for m, d, t in rows))
    ids = [t.trigger_id for t in load_triggers(dubai, [gold]) if t.channel == "canal2"]
    assert ids == ["c2h_2_1107", "c2h_2_1111", "c2h_2_1200"]


def test_delay_draw_is_deterministic_and_from_sample():
    t = Trigger("x", "canal1", "BUY", datetime(2026, 5, 1, tzinfo=timezone.utc), "s")
    h = History("root", [t], {"canal1": [1.0, 2.0, 3.0]}, seed=4)
    a, b = h.observed_at(t), h.observed_at(t)
    assert a == b and (a - t.published_at).total_seconds() in (1.0, 2.0, 3.0)


def test_canal1_texts_join_open_basket_but_stickers_always_open(monkeypatch):
    import research.history_replay as hr
    base = datetime(2026, 5, 1, 10, tzinfo=timezone.utc)

    def sig(i, minute, ch="canal1", kind="text_only"):
        return (Trigger(f"t{i}", ch, "BUY", base, "s", kind),
                SimpleNamespace(channel=ch, observed_at=base.replace(minute=minute)), base.replace(hour=20))

    monkeypatch.setattr(hr, "make_trigger_path", lambda s, g, m, c, e: (s, e))
    closes = {0: 30, 31: 50}

    def ev(path, genome):
        close = base.replace(minute=closes.get(path.observed_at.minute, path.observed_at.minute + 1))
        return SimpleNamespace(filled_volume=0.01, exits=[SimpleNamespace(closed_at=close)], pnl_eur=1.0,
                               max_floating_drawdown_eur=0.5, blockers=())

    items = [sig(0, 0, kind="sticker"), sig(1, 10), sig(2, 31), sig(3, 40, "canal2", "text_now"),
             sig(4, 45, kind="sticker"), sig(5, 46)]
    rows = simulate_day(items, {"canal1": None, "canal2": None}, {"canal1": ev, "canal2": ev}, None, None)
    status = {r["id"]: r["status"] for r in rows}
    # t0 sticker opens (closes :30); t1 text at :10 joins it; t2 text at :31 opens (closes :50);
    # t4 sticker at :45 opens although t2 is open; t5 text at :46 joins the open baskets
    assert status == {"t0": "filled", "t1": "companion", "t2": "filled", "t3": "filled",
                      "t4": "filled", "t5": "companion"}
