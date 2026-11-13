from tradebot.broker import PaperBroker
from tradebot.engine import Engine, EngineConfig
from tradebot.run_live_paper import Runner


def mk(tmp_path, **kw):
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    return Engine(broker=PaperBroker(cash=100.0), cfg=cfg, **kw)


def boom():
    raise RuntimeError("offline")


def feed(prices):
    it = iter(prices)
    return lambda: float(next(it))


def test_no_news_means_no_buy_but_price_is_tracked(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, price_fn=feed([100, 100, 100, 100, 100, 101, 102]), news_fn=boom, fg_fn=boom)
    for i in range(7):
        r.step(1000.0 + i * 30)
    assert not e.positions and r.last_price == 102.0
    assert (tmp_path / "s.json").exists()


def test_stop_loss_still_works_when_news_fail(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, price_fn=feed([100, 100, 100, 100, 100, 101, 102]), news_fn=lambda: 0.9, fg_fn=lambda: 20)
    for i in range(7):
        r.step(1000.0 + i * 30)
    assert "BTC-EUR" in e.positions
    # Nachrichten fallen aus und sind veraltet; Kurs bricht ein -> Stop-Loss muss greifen
    r.news_fn, r.price_fn = boom, feed([90])
    r.step(1000.0 + 7200)
    assert not e.positions
    assert e.broker.trades[-1]["side"] == "sell"


def test_price_failure_skips_tick_without_crash(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, price_fn=boom, news_fn=lambda: 0.9, fg_fn=lambda: 20)
    r.step(1000.0)
    assert not e.positions and not e.prices
    assert any("kein Kurs" in line for line in e.log)


def test_stale_news_do_not_trigger_buy(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, price_fn=feed([100, 100, 100, 100, 100, 101, 102]), news_fn=lambda: 0.9, fg_fn=lambda: 20)
    r.step(0.0)                      # einmal erfolgreich
    r.news_fn = boom
    for i in range(1, 7):            # danach > 1 h alt
        r.step(4000.0 + i * 30)
    assert not e.positions


def test_restore_continues_with_open_position(tmp_path):
    e = mk(tmp_path)
    for p in [100, 100, 100, 100, 100, 101, 102]:
        e.tick(float(p), 0.9)
    e.start_day("2026-11-13", 100.0)
    e.save(102.0)
    cash, pos = e.broker.cash, e.positions["BTC-EUR"]

    e2 = mk(tmp_path)
    assert e2.restore()
    assert e2.broker.cash == cash and e2.positions["BTC-EUR"] == pos
    assert e2.broker.holdings == e.broker.holdings and e2.day == "2026-11-13"
    e2.tick(90.0, 0.0)               # Stop-Loss greift auch nach Neustart
    assert not e2.positions


def test_restore_rejects_missing_or_broken_file(tmp_path):
    e = mk(tmp_path)
    assert not e.restore()
    (tmp_path / "s.json").write_text("{kaputt")
    assert not e.restore()
    (tmp_path / "s.json").write_text('{"equity": 1}')
    assert not e.restore()
    assert e.broker.cash == 100.0


def test_new_day_resets_daily_limit(tmp_path):
    e = mk(tmp_path)
    e.start_day("2026-11-13", 100.0)
    e.risk.halted = True
    e.start_day("2026-11-13", 90.0)
    assert e.risk.halted             # gleicher Tag: bleibt gestoppt
    e.start_day("2026-11-14", 90.0)
    assert not e.risk.halted and e.risk.day_start_equity == 90.0


def test_log_lines_are_echoed(tmp_path):
    seen = []
    e = mk(tmp_path, on_log=seen.append)
    e._say("hallo")
    assert seen and seen[0].endswith("hallo")
