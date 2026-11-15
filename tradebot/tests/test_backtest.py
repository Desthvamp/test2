from tradebot.backtest import replay, report


def candles(prices, start=1_700_000_000, step=300):
    return [(start + i * step, float(p)) for i, p in enumerate(prices)]


def test_replay_buys_dip_after_turn_and_reports():
    # flach -> Dreh nach oben (Kauf) -> Rückgang -> Dreh (Nachkauf) -> Erholung
    prices = [100] * 12 + [100.5, 101] + [99, 97.5, 97, 97, 97, 97] + [98.5, 99, 100, 101, 102]
    c = candles(prices)
    e, events = replay(c, interval=5, fear_greed=13)
    kinds = [text.split()[0] for _, text in events if text.split()[0] in ("KAUF", "NACHKAUF", "VERKAUF")]
    assert kinds == ["KAUF", "NACHKAUF"]
    lines = []
    report(c, e, events, out=lines.append)
    assert any("NACHKAUF" in x for x in lines) and any("Stand am Ende" in x for x in lines)
    assert e.broker.equity({"BTC-EUR": 102.0}) > 100.0
