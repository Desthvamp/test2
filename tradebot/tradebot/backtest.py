"""Spielt echte Kraken-Kurse der letzten Tage durch die aktuellen Regeln (Paper, ohne Börse).

    python -m tradebot.backtest            # 5-Minuten-Kurse, rund 60 Stunden zurück
    python -m tradebot.backtest 15         # 15-Minuten-Kurse, rund 7 Tage zurück
    python -m tradebot.backtest 60         # Stunden-Kurse, rund 30 Tage zurück

Grenzen: Es zählt nur der Schlusskurs jeder Kerze (der echte Bot sieht alle 30 Sekunden einen Kurs),
Fear&Greed wird für den ganzen Zeitraum gleich angenommen, Nachrichten fehlen. Wenige Tage sind
außerdem viel zu wenig, um zu sagen, ob eine Regel Geld verdient. Das Werkzeug zeigt, WIE sich die
Regeln in echten Kursen verhalten, nicht, ob sie sich lohnen."""
from __future__ import annotations

import sys
import time

from .broker import PaperBroker
from .engine import Engine, EngineConfig
from .risk import RiskConfig
from .strategy import StrategyConfig


def fetch(interval: int = 5, pair: str = "XBTEUR") -> list[tuple[float, float]]:
    """(Schlusszeit, Schlusskurs) je Kerze. Die letzte, noch laufende Kerze bleibt weg."""
    from .kraken import KrakenClient
    r = KrakenClient().public("OHLC", pair=pair, interval=interval)
    rows = next(v for k, v in r.items() if k != "last")
    return [(float(x[0]) + interval * 60, float(x[4])) for x in rows[:-1]]


def replay(candles: list[tuple[float, float]], interval: int = 5, fear_greed: int | None = 13,
           news: float | None = None, risk_cfg: RiskConfig | None = None,
           strat_cfg: StrategyConfig | None = None, cash: float = 100.0):
    """Gibt (engine, ereignisse) zurück; ereignisse = [(zeit, text)]."""
    risk_cfg = risk_cfg or RiskConfig()
    strat_cfg = strat_cfg or StrategyConfig()
    strat_cfg.min_samples = max(3, min(strat_cfg.min_samples, int(strat_cfg.min_history_s / 60 / interval)))
    e = Engine(broker=PaperBroker(cash=cash, fee_pct=risk_cfg.fee_pct), risk_cfg=risk_cfg, strat_cfg=strat_cfg,
               cfg=EngineConfig(kill_file="/nonexistent/KILL", state_file="/nonexistent/state.json"))
    events: list[tuple[float, str]] = []
    now = [0.0]
    e.on_log = lambda line: events.append((now[0], line.split(" ", 2)[2]))
    day = ""
    for ts, price in candles:
        now[0] = ts
        d = time.strftime("%Y-%m-%d", time.localtime(ts))
        if d != day:
            e.start_day(d, e.broker.equity({e.cfg.symbol: price}))
            day = d
        e.tick(price, news, now=ts, fear_greed=fear_greed)
    return e, events


def report(candles, e, events, out=print) -> None:
    def t(ts):
        return time.strftime("%d.%m. %H:%M", time.localtime(ts))
    first, last = candles[0][1], candles[-1][1]
    out(f"Zeitraum {t(candles[0][0])} bis {t(candles[-1][0])}, Kurs {first:.0f} -> {last:.0f} ({last / first - 1:+.1%})")
    for ts, text in events:
        if text.startswith(("KAUF", "NACHKAUF", "VERKAUF", "TEILVERKAUF")):
            out(f"  {t(ts)}  {text}")
    eq = e.broker.equity({e.cfg.symbol: last})
    held = sum(e.broker.holdings.values()) * last
    out(f"Stand am Ende: {eq:.2f} EUR ({eq - 100:+.2f}), davon investiert {held:.2f} EUR, Trades {len(e.broker.trades)}")
    out(f"Zum Vergleich, 100 EUR am Anfang gekauft und gehalten: {100 * last / first * (1 - 0.008):.2f} EUR")


def main() -> int:
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    candles = fetch(interval)
    e, events = replay(candles, interval)
    report(candles, e, events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
