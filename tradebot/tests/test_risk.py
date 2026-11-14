from tradebot.broker import PaperBroker, BrokerError
from tradebot.news import score_text, aggregate
from tradebot.risk import RiskConfig, RiskState, check_daily_limit, open_position, position_size, update_position


def test_position_size_respects_risk_and_cap():
    cfg = RiskConfig()
    qty = position_size(100.0, 50.0, cfg)
    assert qty * 50.0 <= 100 * cfg.max_position_pct + 1e-9
    # Durchgespielt: kaufen, am Stop-Loss verkaufen -> Verlust inkl. Gebuehren hoechstens 2 % (2 EUR)
    b = PaperBroker(cash=100.0, fee_pct=cfg.fee_pct)
    b.buy("X", qty, 50.0)
    b.sell("X", qty, 50.0 * (1 - cfg.stop_loss_pct))
    assert 1.9 < 100.0 - b.cash <= 2.0


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
    assert update_position(pos, 105.0, cfg) is None and not pos.trailing_active  # unter +6 %: noch nicht aktiv
    update_position(pos, 107.0, cfg)
    assert pos.trailing_active
    stop_after_up = pos.stop
    update_position(pos, 110.0, cfg)
    assert pos.stop > stop_after_up
    update_position(pos, 108.0, cfg)          # Kurs faellt, Stop bleibt stehen
    assert pos.stop == 110.0 * (1 - cfg.trail_distance_pct)
    assert update_position(pos, 106.0, cfg) == "stop"
    # gesicherter Gewinn deckt bei Aktivierung etwa die Gebuehren beider Seiten
    assert 1.06 * (1 - cfg.trail_distance_pct) * (1 - cfg.fee_pct) > (1 + cfg.fee_pct) * 0.995


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
    # Wortgrenzen: 'bank' ist kein 'ban', 'against' kein 'gain', 'fallout' kein 'fall'
    assert score_text("Bank rules against issue, fallout unclear") == 0.0


def test_news_summary_ignores_neutral_and_needs_min_signal():
    from tradebot.news import summarize
    neutral = [f"Neutral headline {i}" for i in range(30)]
    s = summarize(neutral + ["Bitcoin surges", "ETF inflows soar", "Solana rallies", "Exchange hacked"])
    assert (s.pos, s.neg, s.total) == (3, 1, 34) and s.score == 0.5
    # zu wenige eindeutige Schlagzeilen -> neutral statt Zufallswert
    assert summarize(neutral + ["Bitcoin surges", "Exchange hacked"]).score == 0.0
    # doppelte Schlagzeilen zaehlen einmal
    assert summarize(["Bitcoin surges"] * 5).pos == 1


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


def test_real_headlines_from_november_2026():
    """Echte Schlagzeilen (Cointelegraph/CoinDesk, 14.11.2026). Erwartet wird nur das Vorzeichen."""
    negative = [
        "Bitcoin plunges to $68,000 as Anthropic IPO frenzy adds to market jitters",
        "Bitcoin slides below $70K amid thin liquidity, ETF outflows",
        "Bitcoin ETFs shed $2.35B in 2 weeks, BlackRock’s losing streak hits 11",
        "Bitcoin dips below $70,000 as Dollar Index hits 19-month high",
        "Gemini’s quarterly retail trading volume sinks to 3-year low",
        "Unocoin keeps deposits and withdrawals halted over suspect transfers",
        # frueher faelschlich positiv wegen 'rally':
        "Live updates: Bitcoin eyes bear market lows as late summer rally mostly wiped out",
    ]
    positive = ["Jane Street boosts Fidelity Bitcoin ETF holdings nearly 8x, trims IBIT"]
    neutral = ["Trump nominates Hester Peirce successor among SEC, CFTC picks",
               "DTCC adds Solana to Wall Street tokenization plans, targeting 2027 rollout"]
    assert all(score_text(h) < 0 for h in negative), [h for h in negative if score_text(h) >= 0]
    assert all(score_text(h) > 0 for h in positive)
    assert all(score_text(h) == 0 for h in neutral)
