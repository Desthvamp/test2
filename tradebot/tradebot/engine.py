"""Hauptlogik: pro Tick Exits pruefen, dann ggf. neue Position eroeffnen."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .broker import BrokerError, PaperBroker
from .risk import (Position, RiskConfig, RiskState, check_daily_limit,
                   open_position, position_size, update_position)


@dataclass
class EngineConfig:
    symbol: str = "BTC-EUR"
    entry_news_threshold: float = 0.5   # Stimmung muss mindestens so positiv sein
    entry_momentum_pct: float = 0.005   # zusaetzlich: Kurs steigt um mind. 0.5 % ueber Lookback
    lookback: int = 5
    kill_file: str = "KILL"             # existiert diese Datei -> keine neuen Trades, Positionen schliessen
    state_file: str = "state.json"


@dataclass
class Engine:
    broker: PaperBroker
    risk_cfg: RiskConfig = field(default_factory=RiskConfig)
    cfg: EngineConfig = field(default_factory=EngineConfig)
    positions: dict[str, Position] = field(default_factory=dict)
    prices: list[float] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.risk = RiskState(day_start_equity=self.broker.cash)

    def _say(self, msg: str) -> None:
        self.log = (self.log + [msg])[-200:]

    def kill_switch_on(self) -> bool:
        return Path(self.cfg.kill_file).exists()

    def tick(self, price: float, news_score: float) -> None:
        self.prices = (self.prices + [price])[-(self.cfg.lookback + 1):]
        symbol = self.cfg.symbol
        equity = self.broker.equity({symbol: price})
        check_daily_limit(self.risk, equity, self.risk_cfg)

        # 1) Bestehende Position verwalten
        pos = self.positions.get(symbol)
        if pos:
            reason = update_position(pos, price, self.risk_cfg)
            if self.kill_switch_on():
                reason = reason or "kill_switch"
            if reason:
                self.broker.sell(symbol, pos.qty, price)
                del self.positions[symbol]
                self._say(f"VERKAUF {symbol} @ {price:.2f} ({reason})")

        # 2) Neue Position nur bei freien Regeln
        if self.risk.halted or self.kill_switch_on() or symbol in self.positions:
            return
        if len(self.positions) >= self.risk_cfg.max_open_positions:
            return
        if len(self.prices) <= self.cfg.lookback:
            return
        momentum = price / self.prices[0] - 1
        if news_score < self.cfg.entry_news_threshold or momentum < self.cfg.entry_momentum_pct:
            return
        qty = position_size(equity, price, self.risk_cfg)
        qty = min(qty, self.broker.cash / (price * (1 + self.risk_cfg.fee_pct)))
        if qty * price < 1:
            return
        try:
            self.broker.buy(symbol, qty, price)
        except BrokerError as e:
            self._say(f"Kauf abgelehnt: {e}")
            return
        self.positions[symbol] = open_position(symbol, qty, price, self.risk_cfg)
        self._say(f"KAUF {symbol} {qty:.6f} @ {price:.2f} SL {self.positions[symbol].stop:.2f} TP {self.positions[symbol].take_profit:.2f}")

    def snapshot(self, price: float) -> dict:
        return {
            "equity": round(self.broker.equity({self.cfg.symbol: price}), 2),
            "cash": round(self.broker.cash, 2),
            "halted": self.risk.halted,
            "halt_reason": self.risk.reason,
            "kill_switch": self.kill_switch_on(),
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "trades": self.broker.trades[-50:],
            "log": self.log[-50:],
        }

    def save(self, price: float) -> None:
        tmp = self.cfg.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(price), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.cfg.state_file)
