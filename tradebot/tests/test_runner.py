from tradebot.broker import PaperBroker
from tradebot.engine import Engine, EngineConfig
from tradebot.news import NewsSummary
from tradebot.run_live_paper import Runner

GOOD = lambda: NewsSummary(0.9, 9, 0, 40)  # noqa: E731
FLAT = [100.0] * 40


def mk(tmp_path):
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    return Engine(broker=PaperBroker(cash=100.0), cfg=cfg)


def boom():
    raise RuntimeError("offline")


def run(r, prices, start=0.0):
    """Ein Schritt pro Minute. Gibt die naechste Zeit zurueck."""
    t = start
    for p in prices:
        r.price_fn = lambda p=p: float(p)
        r.step(t)
        t += 60
    return t


def test_buys_with_news_and_writes_status(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, news_fn=GOOD, fg_fn=lambda: 50)
    run(r, FLAT + [101.0])
    assert "BTC-EUR" in e.positions
    assert e.status["price"] == 101.0 and e.status["fear_greed"] == 50
    assert "9 positiv" in e.status["news_info"]
    assert all("Kurs " not in line for line in e.log)   # Kurszeilen gehoeren nicht ins Ereignis-Log


def test_no_news_means_no_buy_but_price_is_tracked(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, news_fn=boom, fg_fn=boom)
    run(r, FLAT + [101.0])
    assert not e.positions and r.last_price == 101.0
    assert e.status["news"] is None
    assert sum("Nachrichten nicht abrufbar" in line for line in e.log) == 1
    assert (tmp_path / "s.json").exists()


def test_stop_loss_still_works_when_news_fail(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, news_fn=GOOD, fg_fn=lambda: 50)
    t = run(r, FLAT + [101.0])
    assert "BTC-EUR" in e.positions
    r.news_fn = boom                                    # Nachrichten fallen aus und veralten
    run(r, [90.0], start=t + 7200)
    assert not e.positions and e.broker.trades[-1]["side"] == "sell"


def test_price_failure_skips_tick_without_crash(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, price_fn=boom, news_fn=GOOD, fg_fn=lambda: 50)
    r.step(0.0); r.step(30.0)
    assert not e.positions and not e.history
    assert sum("Kein Kurs" in line for line in e.log) == 1


def test_stale_news_do_not_trigger_buy(tmp_path):
    e = mk(tmp_path)
    r = Runner(e, news_fn=GOOD, fg_fn=lambda: 50)
    r.price_fn = lambda: 100.0
    r.step(0.0)                                         # einmal erfolgreich
    r.news_fn = boom
    run(r, FLAT + [101.0], start=4000.0)                # danach ueber 1 Std alt
    assert not e.positions
