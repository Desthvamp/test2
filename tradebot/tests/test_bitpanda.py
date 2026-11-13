import pytest

from tradebot.bitpanda import BitpandaBroker, _qty_str
from tradebot.broker import BrokerError


def fake(limit=True, buy_price="100.0", sell_price="100.0", log=None):
    log = log if log is not None else []

    def t(method, path, body):
        log.append((method, path, body))
        if path == "/v1/trading-limits":
            if not limit:
                raise BrokerError("404")
            return {"data": {"buy_limit": 50, "sell_limit": 50}}
        if path == "/v1/currencies":
            return {"data": [{"id": "eur-id", "symbol": "EUR"}]}
        if path.startswith("/v1/assets"):
            return {"data": [{"id": "btc-id", "symbol": "BTC", "buy_active": True, "sell_active": True}]}
        if path == "/v1/quotes":
            px = buy_price if body["side"] == "BUY" else sell_price
            return {"data": {"quote_id": "q1", "quote": {"price_with_fee": px}}}
        if path == "/v1/quotes/q1/accept":
            side = [b for m, p, b in log if p == "/v1/quotes"][-1]
            px = buy_price if side["side"] == "BUY" else sell_price
            return {"data": {"execution": {"quantity": side["quantity"], "price_with_fee": px, "fee": "0.1"}}}
        raise AssertionError(path)
    return t


def mk(**kw):
    return BitpandaBroker(fake(**kw), {"BTC-EUR": "BTC"}, 100.0, live=True)


def test_refuses_without_live_flag():
    with pytest.raises(BrokerError):
        BitpandaBroker(fake(), {"BTC-EUR": "BTC"}, 100.0, live=False)


def test_refuses_without_trading_limit():
    with pytest.raises(BrokerError):
        mk(limit=False)


def test_buy_sell_roundtrip_updates_cash():
    b = mk()
    b.buy("BTC-EUR", 0.5, 100.0)
    assert round(b.cash, 2) == 50.0 and b.holdings["BTC-EUR"] == 0.5
    b.sell("BTC-EUR", 0.5, 100.0)
    assert round(b.cash, 2) == 100.0 and not b.holdings


def test_buy_rejected_on_bad_slippage_without_accepting():
    log = []
    b = BitpandaBroker(fake(buy_price="110.0", log=log), {"BTC-EUR": "BTC"}, 100.0, live=True)
    with pytest.raises(BrokerError):
        b.buy("BTC-EUR", 0.5, 100.0)
    assert not any(p.endswith("/accept") for _, p, _ in log)
    assert b.cash == 100.0


def test_qty_rounding_down_and_tiny():
    assert _qty_str(0.123456789) == "0.12345678"
    with pytest.raises(BrokerError):
        _qty_str(0.000000001)
