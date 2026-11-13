import pytest

from tradebot.broker import PaperBroker
from tradebot.engine import Engine, EngineConfig


def mk(tmp_path, **kw):
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    return Engine(broker=PaperBroker(cash=100.0), cfg=cfg, **kw)


def warm(e, minutes=40, price=100.0, news=0.9, fg=50, start=0.0):
    """Sammelt `minutes` Minuten flache Kurse (1 Tick pro Minute). Gibt die naechste Zeit zurueck."""
    t = start
    for _ in range(minutes):
        e.tick(price, news, now=t, fear_greed=fg)
        t += 60
    return t


def test_buys_after_warmup_and_takes_profit(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    assert not e.positions                    # flacher Kurs -> kein Trend -> kein Kauf
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    assert "BTC-EUR" in e.positions
    e.tick(120.0, 0.0, now=t + 60, fear_greed=50)
    assert not e.positions
    assert "Gewinnmitnahme" in e.log[-1] and "Ergebnis +" in e.log[-1]
    e.save(120.0)
    assert (tmp_path / "s.json").exists()


def test_no_buy_before_enough_history(tmp_path):
    e = mk(tmp_path)
    t = warm(e, minutes=10)
    e.tick(105.0, 0.9, now=t, fear_greed=50)
    assert not e.positions
    assert not e.signal[0].ok and "Kursdaten" in e.signal[0].text


@pytest.mark.parametrize("news,fg,expect", [
    (0.9, 50, True),     # normale Lage, gute Nachrichten
    (0.1, 50, False),    # normale Lage, Nachrichten zu schwach
    (0.0, 20, True),     # extreme Angst: neutrale Nachrichten reichen
    (-0.3, 20, False),   # extreme Angst, aber negative Nachrichten
    (0.9, 80, False),    # ueberhitzt
    (None, 20, False),   # keine Nachrichten -> nie kaufen
    (0.9, None, True),   # Fear&Greed fehlt -> normale Schwelle
    (0.1, None, False),
])
def test_entry_rules(tmp_path, news, fg, expect):
    e = mk(tmp_path)
    t = warm(e, news=news, fg=fg)
    e.tick(101.0, news, now=t, fear_greed=fg)
    assert ("BTC-EUR" in e.positions) is expect


def test_no_buy_when_price_below_average_or_falling(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(99.0, 0.9, now=t, fear_greed=50)             # unter dem Schnitt
    assert not e.positions
    e2 = mk(tmp_path)
    t = warm(e2, minutes=25, price=100.0)
    t = warm(e2, minutes=15, price=102.0, start=t)      # Anstieg ist schon 15 Min alt ...
    e2.positions.clear(); e2.broker.holdings.clear(); e2.broker.cash = 100.0
    e2.tick(101.9, 0.9, now=t, fear_greed=50)           # ... jetzt faellt er wieder
    assert not e2.positions


def test_cooldown_after_exit(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(95.5, 0.9, now=t + 60, fear_greed=50)        # Stop-Loss (Stop liegt bei 95.95)
    assert not e.positions and "Stop-Loss" in e.log[-1] and "Ergebnis -" in e.log[-1]
    assert 97.8 < e.broker.cash < 98.1 and not e.risk.halted   # rund 2 % Verlust, kein Tageslimit
    t = warm(e, minutes=70, price=95.5, start=t + 120)  # alte, hoehere Kurse fallen aus dem Stundenfenster
    e.tick(96.5, 0.9, now=t, fear_greed=50)             # Signal waere gut, aber Pause
    assert not e.positions and any("Pause" in c.text and not c.ok for c in e.signal)
    assert all(c.ok for c in e.signal if "Pause" not in c.text)
    t = warm(e, minutes=40, price=95.5, start=t + 6 * 3600)
    e.tick(96.5, 0.9, now=t, fear_greed=50)
    assert "BTC-EUR" in e.positions


def test_kill_switch_blocks_entries_and_closes_position(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    assert e.positions
    (tmp_path / "KILL").write_text("stop")
    e.tick(101.5, 0.9, now=t + 60, fear_greed=50)
    assert not e.positions and "Not-Aus" in e.log[-1]
    e.tick(103.0, 0.9, now=t + 120, fear_greed=50)
    assert not e.positions


def test_daily_limit_blocks_new_entries(tmp_path):
    e = mk(tmp_path)
    e.broker.cash = 94.0                                # -6 % seit Tagesbeginn
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    assert not e.positions and e.risk.halted
    assert sum("Keine neuen Käufe" in line for line in e.log) == 1


def test_restore_continues_with_open_position(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.start_day("2026-11-13", 100.0)
    e.save(101.0)
    e2 = mk(tmp_path)
    assert e2.restore()
    assert e2.broker.cash == e.broker.cash and e2.positions == e.positions
    assert e2.broker.holdings == e.broker.holdings and e2.day == "2026-11-13"
    assert e2.history == e.history and e2.start_equity == 100.0
    e2.tick(90.0, 0.0, now=t + 60)                      # Stop-Loss greift auch nach Neustart
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
    assert e.risk.halted                                # gleicher Tag: bleibt gestoppt
    e.start_day("2026-11-14", 90.0)
    assert not e.risk.halted and e.risk.day_start_equity == 90.0


def test_log_echo_and_dedupe(tmp_path):
    seen = []
    e = mk(tmp_path, on_log=seen.append)
    e._say("hallo"); e._say("hallo"); e._say("nur terminal", log=False)
    assert len(seen) == 3 and len(e.log) == 1 and e.log[0].endswith("hallo")


def test_restore_state_from_older_version_without_cost(tmp_path):
    import json
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.save(101.0)
    d = json.loads((tmp_path / "s.json").read_text())
    del d["resume"]["stops"]
    for p in d["resume"]["positions"].values():
        del p["cost"]
    (tmp_path / "s.json").write_text(json.dumps(d))
    e2 = mk(tmp_path)
    assert e2.restore()
    assert e2.positions["BTC-EUR"].cost == pytest.approx(e.positions["BTC-EUR"].cost)
    e2.tick(95.5, 0.0, now=t + 60)
    assert "Ergebnis -" in e2.log[-1] and float(e2.log[-1].split("Ergebnis ")[1].split()[0]) > -2.5
