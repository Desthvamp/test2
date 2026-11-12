from tradebot.broker import PaperBroker, BrokerError
from tradebot.engine import Engine, EngineConfig
from tradebot.news import score_text, aggregate
from tradebot.risk import RiskConfig, RiskState, check_daily_limit, open_position, position_size, update_position


def test_position_size_respects_risk_and_cap():
    cfg = RiskConfig()
    qty = position_size(100.0, 50.0, cfg)
    # Stop-Loss-Treffer darf hoechstens 2 % (2 EUR) kosten
    assert qty * 50.0 * cfg.stop_loss_pct <= 100 * cfg.risk_per_trade_pct + 1e-9
    assert qty * 50.0 <= 100 * cfg.max_position_pct + 1e-9


def test_stop_loss_triggers():
    cfg = RiskConfig()
    pos = open_position("X", 1, 100.0, cfg)
    assert update_position(pos, 94.0, cfg) == "stop"


def test_take_profit_triggers():
    cfg = RiskConfig()
    pos = open_position("X", 1, 100.0, cfg)
    assert update_position(pos, 111.0, cfg) == "take_profit"


def test_trailing_stop_only_moves_up_and_locks_gains():
    cfg = RiskConfig(take_profit_pct=1.0)
    pos = open_position("X", 1, 100.0, cfg)
    update_position(pos, 105.0, cfg)  # Trailing aktiv
    stop_after_up = pos.stop
    update_position(pos, 108.0, cfg)
    assert pos.stop > stop_after_up
    assert update_position(pos, 104.0, cfg) == "stop"
    assert pos.stop >= 100.0  # Gewinn gesichert, kein Verlust mehr


def test_daily_limit_halts():
    st = RiskState(day_start_equity=100.0)
    check_daily_limit(st, 94.0, RiskConfig())
    assert st.halted


def test_broker_fees_and_errors():
    b = PaperBroker(cash=100.0, fee_pct=0.01)
    b.buy("X", 1.0, 50.0)
    assert round(b.cash, 2) == 49.5
    b.sell("X", 1.0, 50.0)
    assert round(b.cash, 2) == 99.0  # 100 - 0.5 - (50 - 0.5)
    try:
        b.sell("X", 1.0, 50.0)
        assert False
    except BrokerError:
        pass


def test_news_scoring():
    assert score_text("Bitcoin ETF inflow record, bullish rally") > 0
    assert score_text("Exchange hack and lawsuit, bearish crash") < 0
    assert aggregate([]) == 0.0


def test_engine_opens_and_closes(tmp_path):
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    e = Engine(broker=PaperBroker(cash=100.0), cfg=cfg)
    for p in [100, 100, 100, 100, 100, 101, 102]:
        e.tick(float(p), 0.9)
    assert "BTC-EUR" in e.positions
    e.tick(120.0, 0.0)  # Take-Profit
    assert not e.positions
    e.save(120.0)
    assert (tmp_path / "s.json").exists()


def test_kill_switch_blocks_entries(tmp_path):
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    cfg = EngineConfig(kill_file=str(kill), state_file=str(tmp_path / "s.json"))
    e = Engine(broker=PaperBroker(cash=100.0), cfg=cfg)
    for p in [100, 100, 100, 100, 100, 101, 102]:
        e.tick(float(p), 0.9)
    assert not e.positions


def test_parse_kraken_and_fear_greed():
    import json
    import pytest
    from tradebot.datasources import DataError, parse_fear_greed, parse_kraken_price
    ok = json.dumps({"error": [], "result": {"XXBTZEUR": {"c": ["58123.4", "0.1"]}}}).encode()
    assert parse_kraken_price(ok) == 58123.4
    with pytest.raises(DataError):
        parse_kraken_price(json.dumps({"error": ["EQuery:Unknown asset pair"], "result": {}}).encode())
    assert parse_fear_greed(json.dumps({"data": [{"value": "18"}]}).encode()) == 18
    with pytest.raises(DataError):
        parse_fear_greed(json.dumps({"data": [{"value": "150"}]}).encode())
