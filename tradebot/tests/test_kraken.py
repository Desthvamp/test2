import pytest

from fake_kraken import SECRET, FakeKraken
from test_engine import dip_and_turn, warm
from tradebot.broker import BrokerError, BrokerUncertain
from tradebot.engine import Engine, EngineConfig
from tradebot.kraken import KrakenBroker, KrakenClient, sign

S = "BTC-EUR"


def broker(fake, cash=100.0, **kw):
    return KrakenBroker(fake.client(), cash, live=True, sleep=lambda s: None, **kw)


def engine(tmp_path, fake):
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    return Engine(broker=broker(fake), cfg=cfg)


def tick(e, fake, price, t):
    fake.set_price(price)
    e.tick(price, 0.9, now=t, fear_greed=50)


def bought(tmp_path, fake):
    """Engine mit offener Position: gekauft zu 101, Stop bei 95.9 an der Börse."""
    e = engine(tmp_path, fake)
    t = warm(e)
    tick(e, fake, 101.0, t)
    assert S in e.positions
    return e, t + 60


# ---------- Signatur und Client ----------

def test_signature_matches_example_from_kraken_docs():
    payload = {"nonce": "1616492376594", "ordertype": "limit", "pair": "XBTUSD",
               "price": 37500, "type": "buy", "volume": 1.25}
    secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    assert sign("/0/private/AddOrder", payload, secret) == \
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="


def test_nonce_always_increases_even_if_clock_stands_still():
    fake = FakeKraken()
    c = KrakenClient("key", SECRET, http=fake.http, clock=lambda: 1000.0)
    for _ in range(3):
        c.private("Balance")                # die nachgebaute Börse lehnt nicht steigende Nonces ab
    assert [int(d["nonce"]) for d in fake.sent("Balance")] == [1000000, 1000001, 1000002]


def test_errors_raise_and_warnings_do_not():
    c = KrakenClient("key", SECRET, http=lambda *a: b'{"error":["EGeneral:Permission denied"]}')
    with pytest.raises(BrokerError, match="Permission denied"):
        c.read("Balance")
    c = KrakenClient("key", SECRET, http=lambda *a: b'{"error":["WDeadline:Soon"],"result":{"ZEUR":"1"}}')
    assert c.read("Balance") == {"ZEUR": "1"}
    with pytest.raises(BrokerError, match="Key fehlt"):
        KrakenClient().read("Balance")


# ---------- Broker ----------

def test_start_requires_live_flag_and_enough_money():
    with pytest.raises(BrokerError, match="nicht freigegeben"):
        KrakenBroker(FakeKraken().client(), 100.0)
    with pytest.raises(BrokerError, match="nur 40.00 EUR"):
        broker(FakeKraken(eur=40.0))
    b = broker(FakeKraken())
    assert (b.min_qty, b.price_decimals, b.qty_decimals, b.fee_pct) == (0.00006, 1, 8, 0.008)


def test_buy_is_capped_limit_ioc_and_books_real_fill():
    fake = FakeKraken(price=100.0)
    b = broker(fake)
    fill = b.buy(S, 0.3, 100.0)
    (order,) = fake.sent("AddOrder")
    assert (order["ordertype"], order["timeinforce"], order["price"], order["volume"]) == ("limit", "IOC", "100.5", "0.30000000")
    assert (fill.qty, fill.price) == (0.3, 100.0) and fill.fee == pytest.approx(0.24)
    assert b.cash == pytest.approx(100 - 30.24) and b.holdings[S] == 0.3
    assert fake.eur == pytest.approx(500 - 30.24)


def test_buy_not_executed_when_price_ran_away():
    fake = FakeKraken(price=100.0)
    b = broker(fake)
    fake.set_price(101.0)                   # Markt liegt über unserem Limit von 100.5
    with pytest.raises(BrokerError, match="weggelaufen"):
        b.buy(S, 0.3, 100.0)
    assert b.cash == 100.0 and not b.holdings and fake.btc == 0


def test_buy_limits():
    b = broker(FakeKraken())
    with pytest.raises(BrokerError, match="Mindestmenge"):
        b.buy(S, 0.00001, 100.0)
    with pytest.raises(BrokerError, match="Bot-Kapital"):
        b.buy(S, 2.0, 100.0)
    with pytest.raises(BrokerError, match="Obergrenze"):
        broker(FakeKraken(), max_order_eur=20.0).buy(S, 0.3, 100.0)


def test_stop_is_real_order_and_only_moves_up():
    fake = FakeKraken()
    b = broker(fake)
    b.buy(S, 0.3, 100.0)
    b.set_stop(S, 95.04)
    (stop,) = fake.open_stops()
    assert (stop["descr"]["type"], stop["descr"]["price"], stop["vol"]) == ("sell", "95.0", "0.30000000")
    b.set_stop(S, 95.1)                     # zu kleiner Schritt
    b.set_stop(S, 90.0)                     # nach unten: nie
    assert not fake.sent("AmendOrder") and stop["descr"]["price"] == "95.0"
    b.set_stop(S, 103.77)
    assert fake.sent("AmendOrder")[0]["trigger_price"] == "103.8" and stop["descr"]["price"] == "103.8"
    assert len(fake.open_stops()) == 1      # geändert, nicht neu angelegt


def test_sync_books_stop_filled_by_exchange():
    fake = FakeKraken()
    b = broker(fake)
    b.buy(S, 0.3, 100.0)
    b.set_stop(S, 95.0)
    assert b.sync(S) is None                # noch offen
    fake.trigger_stop()
    fill = b.sync(S)
    assert (fill.qty, fill.price) == (0.3, 95.0)
    assert not b.holdings and not b.stops
    assert b.cash == pytest.approx(100 - 30.24 + 28.5 * 0.992)
    assert b.cash - 100 == pytest.approx(fake.eur - 500, abs=1e-3)     # Bot-Buchhaltung = Kontobewegung


def test_stop_cancelled_by_hand_is_placed_again():
    fake = FakeKraken()
    b = broker(fake)
    b.buy(S, 0.3, 100.0)
    b.set_stop(S, 95.0)
    fake.m_CancelOrder({"txid": b.stops[S]["txid"]})         # jemand löscht den Stop in der Kraken-App
    assert b.sync(S) is None and not b.stops and b.holdings[S] == 0.3
    b.set_stop(S, 95.0)
    assert len(fake.open_stops()) == 1


def test_sell_cancels_stop_then_sells_at_market():
    fake = FakeKraken()
    b = broker(fake)
    b.buy(S, 0.3, 100.0)
    b.set_stop(S, 95.0)
    fake.set_price(110.0)
    fill = b.sell(S, 0.3, 110.0)
    assert (fill.qty, fill.price) == (0.3, 110.0)
    assert not fake.open_stops() and fake.btc == pytest.approx(0) and not b.holdings and not b.stops
    assert b.cash - 100 == pytest.approx(fake.eur - 500, abs=1e-3)


def test_sell_when_exchange_stop_was_faster_does_not_sell_twice():
    fake = FakeKraken()
    b = broker(fake)
    b.buy(S, 0.3, 100.0)
    b.set_stop(S, 95.0)
    fake.trigger_stop()
    fill = b.sell(S, 0.3, 94.0)
    assert fill.price == 95.0 and len(fake.sent("AddOrder")) == 2      # Kauf + Stop, kein Markt-Verkauf
    assert b.cash - 100 == pytest.approx(fake.eur - 500, abs=1e-3)


def test_lost_answer_is_reported_as_uncertain_not_as_failed():
    fake = FakeKraken()
    b = broker(fake)
    fake.fail.add("AddOrder")
    with pytest.raises(BrokerUncertain, match="unklar"):
        b.buy(S, 0.3, 100.0)
    fake.fail.clear()
    b2 = broker(FakeKraken())
    b2.buy(S, 0.3, 100.0)
    b2.c.http.__self__.btc = 0.1            # auf dem Konto fehlt BTC, den der Bot zu haben glaubt
    with pytest.raises(BrokerUncertain, match="statt"):
        b2.sell(S, 0.3, 100.0)


# ---------- Engine + Kraken ----------

def test_engine_buy_places_stop_on_exchange(tmp_path):
    fake = FakeKraken()
    e, _ = bought(tmp_path, fake)
    pos = e.positions[S]
    assert pos.entry == pytest.approx(101.0) and pos.cost == pytest.approx(pos.qty * 101.0 * 1.008)
    (stop,) = fake.open_stops()
    assert stop["descr"]["price"] == "95.9" and float(stop["vol"]) == pytest.approx(pos.qty, abs=1e-8)


def test_engine_trails_stop_on_exchange_and_exchange_closes_position(tmp_path):
    fake = FakeKraken()
    e, t = bought(tmp_path, fake)
    tick(e, fake, 108.0, t)                                  # +6,9 %: Trailing aktiv, Stop 104.76
    (stop,) = fake.open_stops()
    assert stop["descr"]["price"] == "104.8"
    cash_before = e.broker.cash
    fake.trigger_stop()                                      # Bot "schläft", Kraken verkauft
    tick(e, fake, 104.0, t + 60)
    assert not e.positions and not fake.open_stops() and e.last_exit_ts == t + 60
    assert "nachgezogener Stop" in e.log[-1] and "Ergebnis +" in e.log[-1]
    assert e.broker.cash > cash_before and len(fake.sent("AddOrder")) == 2     # kein zweiter Verkauf


def test_engine_take_profit_cleans_up_exchange(tmp_path):
    fake = FakeKraken()
    e, t = bought(tmp_path, fake)
    tick(e, fake, 112.0, t)
    assert not e.positions and "Gewinnmitnahme" in e.log[-1]
    assert not fake.open_stops() and fake.btc == pytest.approx(0, abs=1e-9)
    assert e.broker.cash - 100 == pytest.approx(fake.eur - 500, abs=1e-3)


def test_engine_kill_switch_sells_on_exchange(tmp_path):
    fake = FakeKraken()
    e, t = bought(tmp_path, fake)
    (tmp_path / "KILL").write_text("x")
    tick(e, fake, 101.5, t)
    assert not e.positions and "Not-Aus" in e.log[-1] and not fake.open_stops()


def test_engine_survives_restart_with_stop_on_exchange(tmp_path):
    fake = FakeKraken()
    e, t = bought(tmp_path, fake)
    e.save(101.0)
    e2 = engine(tmp_path, fake)                              # neuer Prozess, gleiche Börse
    assert e2.restore() and e2.broker.stops == e.broker.stops and e2.broker.holdings == e.broker.holdings
    fake.trigger_stop()                                      # ausgelöst, während der Bot aus war
    tick(e2, fake, 95.0, t)
    assert not e2.positions and "Stop an der Börse ausgelöst" in e2.log[-1]
    assert "Ergebnis -2.00 EUR" in e2.log[-1]                # genau die Verlustgrenze von 2 % des Kapitals
    assert e2.broker.cash - 100 == pytest.approx(fake.eur - 500, abs=1e-3)


def test_engine_keeps_position_when_exchange_unreachable(tmp_path):
    fake = FakeKraken()
    e, t = bought(tmp_path, fake)
    fake.fail.add("QueryOrders")
    tick(e, fake, 102.0, t)
    assert S in e.positions and "neuer Versuch" in e.log[-1] and not e.lock
    fake.fail.clear()
    tick(e, fake, 102.0, t + 60)
    assert S in e.positions and len(fake.open_stops()) == 1


def test_engine_locks_after_uncertain_order(tmp_path):
    fake = FakeKraken()
    e = engine(tmp_path, fake)
    t = warm(e)
    fake.fail.add("AddOrder")
    tick(e, fake, 101.0, t)
    assert not e.positions and e.lock and "ACHTUNG" in e.log[-1]
    fake.fail.clear()
    tick(e, fake, 101.5, t + 60)                             # Signal wäre gut, aber gesperrt
    assert not e.positions and "Gesperrt" in e.signal[0].text
    assert len(fake.sent("AddOrder")) == 1 and e.snapshot(101.5)["lock"]


# ---------- Prüfwerkzeug ----------

def test_check_tool_never_places_orders():
    from tradebot.kraken_check import check
    fake, lines = FakeKraken(price=62000.0), []
    assert check(fake.client(), out=lines.append)
    assert not fake.orders                                   # nichts angelegt
    sent = fake.sent("AddOrder")
    assert len(sent) == 2 and all(d["validate"] == "true" for d in sent)
    # selbst als echte Orders harmlos: Mindestmenge und halber Marktpreis
    assert all(d["volume"] == "0.00006" and d["price"] == "31000.0" for d in sent)
    assert any("nicht möglich, weil noch kein BTC" in x for x in lines)
    assert any("500.00 EUR" in x for x in lines) and any("0.80 %" in x for x in lines)


def test_check_tool_reports_missing_permission():
    from tradebot.kraken_check import check
    fake, lines = FakeKraken(), []
    fake.m_Balance = lambda d: (_ for _ in ()).throw(__import__("fake_kraken").Err("EGeneral:Permission denied"))
    assert not check(fake.client(), out=lines.append)
    assert any(x.startswith("✗ Konto lesen") and "Permission denied" in x for x in lines)
    assert not fake.orders


# ---------- Nachkauf mit Kraken ----------

def test_stop_order_follows_added_volume():
    fake = FakeKraken()
    b = broker(fake)
    b.buy(S, 0.3, 100.0)
    b.set_stop(S, 95.0)
    fake.set_price(97.0)
    b.buy(S, 0.3, 97.0)
    b.set_stop(S, 93.6)                                  # nach Nachkauf: mehr Menge, Stop tiefer
    (stop,) = fake.open_stops()
    assert (stop["vol"], stop["descr"]["price"]) == ("0.60000000", "93.6")
    assert fake.sent("AmendOrder")[-1] == {**fake.sent("AmendOrder")[-1], "order_qty": "0.60000000", "trigger_price": "93.6"}
    b.set_stop(S, 90.0)                                  # ohne Mengenänderung nie nach unten
    assert stop["descr"]["price"] == "93.6" and len(fake.sent("AmendOrder")) == 1


def test_engine_add_resizes_stop_on_exchange_and_exchange_closes_all(tmp_path):
    fake = FakeKraken()
    e, t = bought(tmp_path, fake)
    t = dip_and_turn(e, t, tick=lambda p, now: tick(e, fake, p, now))
    pos = e.positions[S]
    assert pos.adds == 1
    (stop,) = fake.open_stops()                          # weiterhin genau eine Stop-Order
    assert float(stop["vol"]) == pytest.approx(pos.qty, abs=1e-8) == pytest.approx(fake.btc, abs=1e-8)
    assert stop["descr"]["price"] == f"{pos.stop:.1f}"
    fake.trigger_stop()
    tick(e, fake, 94.0, t)
    assert not e.positions and not fake.open_stops() and fake.btc == pytest.approx(0, abs=1e-8)
    assert e.broker.cash - 100 == pytest.approx(fake.eur - 500, abs=1e-3)
    assert -4.4 < e.broker.cash - 100 < -3.8             # rund 4 EUR Verlust
