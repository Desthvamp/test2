import pytest

from tradebot.broker import PaperBroker
from tradebot.engine import Engine, EngineConfig
from tradebot.risk import RiskConfig


def STOP():
    """Einstellung MIT Stop-Loss bei -5 % (Standard ist ohne)."""
    return RiskConfig(stop_loss_pct=0.05)


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
    (None, 50, False),   # normale Lage, keine Nachrichten -> nicht kaufen
    (0.0, 20, True),     # extreme Angst: Nachrichten zählen nicht ...
    (-0.8, 20, True),    # ... auch sehr schlechte nicht
    (None, 20, True),    # ... auch fehlende nicht
    (-0.8, 26, False),   # knapp über der Angst-Grenze gilt wieder die normale Schwelle
    (0.9, 80, False),    # überhitzt
    (0.9, None, True),   # Fear&Greed fehlt -> normale Schwelle
    (0.1, None, False),
    (-0.8, None, False),
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


def test_no_pause_after_exit_by_default(tmp_path):
    e = mk(tmp_path, risk_cfg=STOP())
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(95.5, 0.9, now=t + 60, fear_greed=50)        # Stop-Loss (Stop liegt bei 95.95)
    assert not e.positions and "Stop-Loss" in e.log[-1] and "Ergebnis -" in e.log[-1]
    assert 97.8 < e.broker.cash < 98.1 and not e.risk.halted   # rund 2 % Verlust, kein Tageslimit
    assert not any("Pause" in c.text for c in e.signal)
    e.tick(95.6, 0.9, now=t + 120, fear_greed=50)       # direkt danach: Kurs liegt unter dem Schnitt -> kein Kauf
    assert not e.positions
    t = warm(e, minutes=70, price=95.5, start=t + 180)  # alte, höhere Kurse fallen aus dem Stundenfenster
    e.tick(96.5, 0.9, now=t, fear_greed=50)             # gut eine Stunde später dreht der Kurs -> wieder Kauf
    assert "BTC-EUR" in e.positions


def test_pause_can_be_configured(tmp_path):
    from tradebot.strategy import StrategyConfig
    e = mk(tmp_path, risk_cfg=STOP(), strat_cfg=StrategyConfig(cooldown_s=6 * 3600))
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(95.5, 0.9, now=t + 60, fear_greed=50)
    t = warm(e, minutes=70, price=95.5, start=t + 120)
    e.tick(96.5, 0.9, now=t, fear_greed=50)             # Signal wäre gut, aber Pause
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
    e = mk(tmp_path, risk_cfg=STOP())
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.start_day("2026-11-13", 100.0)
    e.save(101.0)
    e2 = mk(tmp_path, risk_cfg=STOP())
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
    e = mk(tmp_path, risk_cfg=STOP())
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.save(101.0)
    d = json.loads((tmp_path / "s.json").read_text())
    del d["resume"]["stops"]
    for p in d["resume"]["positions"].values():
        del p["cost"]
    (tmp_path / "s.json").write_text(json.dumps(d))
    e2 = mk(tmp_path, risk_cfg=STOP())
    assert e2.restore()
    assert e2.positions["BTC-EUR"].cost == pytest.approx(e.positions["BTC-EUR"].cost)
    e2.tick(95.5, 0.0, now=t + 60)
    assert "Ergebnis -" in e2.log[-1] and float(e2.log[-1].split("Ergebnis ")[1].split()[0]) > -2.5


def test_fear_rule_can_be_switched_off(tmp_path):
    from tradebot.strategy import StrategyConfig
    e = mk(tmp_path, strat_cfg=StrategyConfig(fear_ignores_news=False))
    t = warm(e, news=-0.8, fg=20)
    e.tick(101.0, -0.8, now=t, fear_greed=20)
    assert not e.positions


def test_fear_still_needs_price_turning_up(tmp_path):
    e = mk(tmp_path)
    t = warm(e, news=-0.8, fg=13)
    e.tick(99.5, -0.8, now=t, fear_greed=13)            # Kurs fällt weiter -> kein Kauf
    assert not e.positions
    assert any("zählen bei extremer Angst nicht" in c.text and c.ok for c in e.signal)
    assert any("Trend" in c.text and not c.ok for c in e.signal)


# ---------- Nachkauf ----------

def dip_and_turn(e, t, tick=None):
    """Nach dem Kauf zu 101: Kurs fällt auf 97, bleibt dort, dreht dann auf 98.5 (Nachkauf-Zone 98.2 bis 99.4).
    Gibt die nächste Zeit zurück."""
    tick = tick or (lambda p, now: e.tick(p, 0.9, now=now, fear_greed=50))
    for p in [99.0] + [97.0] * 17:
        tick(p, t)
        t += 60
        assert e.positions["BTC-EUR"].adds == 0          # fällt oder steht: noch kein Nachkauf
    tick(98.5, t)
    return t + 60


def test_add_after_dip_once_price_turns_up(tmp_path):
    e = mk(tmp_path, risk_cfg=STOP())
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    first = e.positions["BTC-EUR"].qty
    spent = 100.0 - e.broker.cash
    t = dip_and_turn(e, t + 60)
    pos = e.positions["BTC-EUR"]
    assert pos.adds == 1 and "NACHKAUF" in e.log[-1]
    assert pos.qty == pytest.approx(first + first * 101.0 / 98.5)          # gleicher Euro-Betrag
    assert 100.0 - e.broker.cash == pytest.approx(2 * spent, rel=1e-3) == pytest.approx(pos.cost, rel=1e-3)
    assert 98.5 < pos.entry < 101.0 and pos.last_buy == pos.low == 98.5                                         # Durchschnittskurs
    assert pos.stop == pytest.approx(pos.entry * 0.95) and pos.take_profit == pytest.approx(pos.entry * 1.10)
    assert "noch 2 Nachkäufe möglich" in e.signal[0].text


def test_single_add_setting_and_loss_stays_bounded_with_stop(tmp_path):
    e = mk(tmp_path, risk_cfg=RiskConfig(stop_loss_pct=0.05, max_adds=1))
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    t = dip_and_turn(e, t + 60)
    for p in [96.0] * 17 + [96.8]:                       # neuer Rückgang mit Dreh in der Zone: kein zweiter Nachkauf
        e.tick(p, 0.9, now=t, fear_greed=50)
        t += 60
    assert e.positions["BTC-EUR"].adds == 1 and len(e.broker.trades) == 2
    assert "alle Nachkäufe verbraucht" in e.signal[0].text
    e.tick(94.0, 0.9, now=t, fear_greed=50)              # Stop (rund 94.75) greift für die ganze Position
    assert not e.positions and "Stop-Loss" in e.log[-1]
    assert 95.3 < e.broker.cash < 96.2                   # rund 4 EUR Verlust, nicht mehr


def test_add_then_recovery_takes_profit_from_average(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    t = dip_and_turn(e, t + 60)
    e.tick(110.0, 0.9, now=t, fear_greed=50)             # +10 % über dem Schnitt von rund 99.7
    assert not e.positions and "Gewinnmitnahme" in e.log[-1] and e.broker.cash > 103.0


@pytest.mark.parametrize("block", ["off", "halted", "lock", "no_cash"])
def test_add_is_blocked(tmp_path, block):
    from tradebot.risk import RiskConfig
    e = mk(tmp_path, risk_cfg=RiskConfig(max_adds=0)) if block == "off" else mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    t += 60
    for p in [99.0] + [97.0] * 17:
        e.tick(p, 0.9, now=t, fear_greed=50)
        t += 60
    if block == "halted":
        e.risk.halted, e.risk.reason = True, "Test"
    if block == "lock":
        e.lock = "Test"
    if block == "no_cash":
        e.broker.cash = 0.5
    e.tick(98.5, 0.9, now=t, fear_greed=50)
    assert e.positions["BTC-EUR"].adds == 0 and len(e.broker.trades) == 1


def test_no_instant_rebuy_after_sale_but_no_fixed_pause(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(112.0, 0.9, now=t + 60, fear_greed=50)       # Gewinnmitnahme; Signal ist weiter "an"
    assert not e.positions and e.rearm
    assert any("erst einmal erlöschen" in c.text for c in e.signal)
    t += 120
    for _ in range(16):                                 # Kurs steht: nach 15 Min ist das Momentum weg
        e.tick(112.0, 0.9, now=t, fear_greed=50)
        t += 60
    assert not e.positions and not e.rearm              # Signal war aus -> wieder scharf
    e.tick(113.0, 0.9, now=t, fear_greed=50)            # neues Signal, gut eine Viertelstunde nach dem Verkauf
    assert "BTC-EUR" in e.positions


def test_rearm_survives_restart(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(112.0, 0.9, now=t + 60, fear_greed=50)
    e.save(112.0)
    e2 = mk(tmp_path)
    assert e2.restore() and e2.rearm
    e2.tick(112.5, 0.9, now=t + 120, fear_greed=50)
    assert not e2.positions


# ---------- Standard: kein Stop-Loss ----------

def test_default_never_sells_at_a_loss(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    pos = e.positions["BTC-EUR"]
    assert pos.stop == 0 and "kein Stop-Loss" in e.log[-1]
    assert 100.0 - e.broker.cash == pytest.approx(20.0 * 1.008)        # fester Anteil: 20 % des Kapitals
    for i, p in enumerate([95.0, 80.0, 60.0, 40.0]):                   # freier Fall, kein Dreh nach oben
        e.tick(p, 0.9, now=t + 60 * (i + 1), fear_greed=50)
    assert "BTC-EUR" in e.positions and len(e.broker.trades) == 1      # gehalten, nichts verkauft
    assert not any("VERKAUF" in line for line in e.log)
    assert e.broker.equity({"BTC-EUR": 40.0}) < 90.0                   # der Verlust ist nicht begrenzt
    e.tick(112.0, 0.9, now=t + 600, fear_greed=50)                     # Erholung bis zum Ziel
    assert not e.positions and "Gewinnmitnahme" in e.log[-1] and "Ergebnis +" in e.log[-1]


def test_default_trailing_stop_still_secures_gains(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(108.0, 0.9, now=t + 60, fear_greed=50)                      # +6,9 %: ab jetzt gibt es einen Stop
    pos = e.positions["BTC-EUR"]
    assert pos.trailing_active and pos.stop == pytest.approx(108.0 * 0.97)
    e.tick(104.0, 0.9, now=t + 120, fear_greed=50)
    assert not e.positions and "nachgezogener Stop" in e.log[-1] and "Ergebnis +" in e.log[-1]


def test_default_add_keeps_position_without_stop(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    dip_and_turn(e, t + 60)
    pos = e.positions["BTC-EUR"]
    assert pos.adds == 1 and pos.stop == 0 and "kein Stop-Loss" in e.log[-1]
    assert 100.0 - e.broker.cash == pytest.approx(40.0 * 1.008)        # zweimal 20 EUR


def test_restore_applies_todays_stop_setting(tmp_path):
    e = mk(tmp_path, risk_cfg=STOP())                                  # alter Stand: Position mit Stop-Loss
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    assert e.positions["BTC-EUR"].stop == pytest.approx(95.95)
    e.save(101.0)
    e2 = mk(tmp_path)                                                  # neue Version: ohne Stop-Loss
    assert e2.restore() and e2.positions["BTC-EUR"].stop == 0
    e2.tick(90.0, 0.9, now=t + 60, fear_greed=50)
    assert "BTC-EUR" in e2.positions
    e2.tick(108.0, 0.9, now=t + 120, fear_greed=50)                    # ein nachgezogener Stop bleibt erhalten
    e2.save(108.0)
    e3 = mk(tmp_path)
    assert e3.restore() and e3.positions["BTC-EUR"].stop == pytest.approx(108.0 * 0.97)


# ---------- Nachkauf richtet sich nach dem Kursverlauf ----------

def test_add_zone_follows_the_low():
    from tradebot.risk import Position, add_zone
    cfg = RiskConfig()
    pos = Position("X", 1, 100.0, 100.0, 0.0, 110.0, last_buy=100.0, low=97.0)
    assert add_zone(pos, cfg) == pytest.approx((97.9, 98.4))        # 30 % von 3 erholt; höchstens 1,6 % unter Kauf
    pos.low = 94.0
    assert add_zone(pos, cfg) == pytest.approx((95.8, 98.4))        # tieferes Tief -> Zone beginnt tiefer
    pos.low = 99.0
    start, end = add_zone(pos, cfg)
    assert start > end                                              # zu kleiner Rückgang: keine Zone


def test_three_adds_each_after_a_new_dip(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    t = dip_and_turn(e, t + 60)                                     # 1. Nachkauf bei 98.5
    for low, turn in [(96.0, 96.8), (94.0, 94.9), (92.0, 92.9)]:
        for p in [low] * 17 + [turn]:
            e.tick(p, 0.9, now=t, fear_greed=50)
            t += 60
    pos = e.positions["BTC-EUR"]
    assert pos.adds == 3 and [round(x["price"], 1) for x in e.broker.trades] == [101.0, 98.5, 96.8, 94.9]
    assert all(x["qty"] * x["price"] == pytest.approx(20.0, rel=1e-3) for x in e.broker.trades)   # gleiche Beträge
    assert e.broker.cash == pytest.approx(100 - 80 * 1.008, rel=1e-3)
    assert "alle Nachkäufe verbraucht" in e._add_hint(pos)
    # 80 EUR sind investiert und der Kurs liegt 9 % unter dem ersten Kauf: das Tageslimit greift zusätzlich
    assert e.risk.halted and "Tagesverlust" in e.signal[0].text


def test_no_add_on_shallow_dip_or_when_recovery_went_too_far(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    t += 60
    for p in [100.0] * 17 + [100.4]:                    # nur 1 % Rückgang, dann Dreh: lohnt nicht
        e.tick(p, 0.9, now=t, fear_greed=50)
        t += 60
    assert e.positions["BTC-EUR"].adds == 0 and "erst deutlich unter 99.38 fallen" in e.signal[0].text
    for p in [97.0] * 2 + [99.6] * 17 + [99.7]:         # tiefer Rückgang, aber der Kurs ist schon fast zurück
        e.tick(p, 0.9, now=t, fear_greed=50)
        t += 60
    assert e.positions["BTC-EUR"].adds == 0


def test_low_starts_over_when_price_is_back_at_last_buy(tmp_path):
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(96.0, 0.9, now=t + 60, fear_greed=50)
    assert e.positions["BTC-EUR"].low == 96.0
    e.tick(101.5, 0.9, now=t + 120, fear_greed=50)      # wieder über dem Kauf: alter Rückgang ist erledigt
    assert e.positions["BTC-EUR"].low == 101.5
    e.tick(100.9, 0.9, now=t + 180, fear_greed=50)
    assert e.positions["BTC-EUR"].low == 100.9 and e.positions["BTC-EUR"].adds == 0


def test_restore_position_from_version_without_low(tmp_path):
    import json
    e = mk(tmp_path)
    t = warm(e)
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    e.tick(97.0, 0.9, now=t + 60, fear_greed=50)
    e.save(97.0)
    d = json.loads((tmp_path / "s.json").read_text())
    for p in d["resume"]["positions"].values():
        del p["last_buy"], p["low"]
    (tmp_path / "s.json").write_text(json.dumps(d))
    e2 = mk(tmp_path)
    assert e2.restore()
    pos = e2.positions["BTC-EUR"]
    assert pos.last_buy == 101.0 and pos.low == 97.0    # Tief aus dem gespeicherten Kursverlauf
