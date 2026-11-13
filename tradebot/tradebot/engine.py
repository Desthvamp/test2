"""Hauptlogik: pro Tick erst die offene Position absichern, dann ggf. eine neue eröffnen."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .broker import BrokerError, BrokerUncertain, Fill, PaperBroker
from .risk import (Position, RiskConfig, RiskState, check_daily_limit,
                   open_position, position_size, update_position)
from .strategy import Check, StrategyConfig, entry_signal

EXIT_TEXT = {"stop": "Stop-Loss", "take_profit": "Gewinnmitnahme", "kill_switch": "Not-Aus",
             "exchange": "Stop an der Börse ausgelöst"}


@dataclass
class EngineConfig:
    symbol: str = "BTC-EUR"
    kill_file: str = "KILL"             # existiert diese Datei -> keine neuen Trades, Positionen schließen
    state_file: str = "state.json"


@dataclass
class Engine:
    broker: PaperBroker          # oder KrakenBroker, gleiche Schnittstelle
    risk_cfg: RiskConfig = field(default_factory=RiskConfig)
    cfg: EngineConfig = field(default_factory=EngineConfig)
    strat_cfg: StrategyConfig = field(default_factory=StrategyConfig)
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[tuple[float, float]] = field(default_factory=list)   # (Zeit, Kurs)
    log: list[str] = field(default_factory=list)                       # nur Ereignisse
    on_log: Callable[[str], None] | None = None   # z. B. print, damit man im Terminal etwas sieht
    day: str = ""                                  # Kalendertag, für den das Tageslimit gilt
    last_exit_ts: float = 0.0
    signal: list[Check] = field(default_factory=list)
    status: dict = field(default_factory=dict)     # letzter Marktstand fürs Dashboard

    def __post_init__(self):
        self.risk = RiskState(day_start_equity=self.broker.cash)
        self.start_equity = self.broker.cash
        self._last_msg = ""
        self.lock = ""   # gesetzt, wenn der Zustand an der Börse unklar ist; nur ein Neustart hebt das auf

    def _close(self, pos: Position, fill: Fill, reason: str, now: float) -> None:
        """Verbucht einen Verkauf in der Position und meldet das Ergebnis."""
        symbol = pos.symbol
        share = min(1.0, fill.qty / pos.qty)
        result = fill.net - pos.cost * share
        label = "nachgezogener Stop" if reason in ("stop", "exchange") and pos.trailing_active else EXIT_TEXT[reason]
        left = self.broker.holdings.get(symbol, 0.0)
        if left >= self.broker.min_qty:      # nur ein Teil wurde verkauft, der Rest bleibt abgesichert
            pos.qty, pos.cost = left, pos.cost * (1 - share)
            self._say(f"TEILVERKAUF {symbol} {fill.qty:.6f} @ {fill.price:.2f} ({label}), "
                      f"Ergebnis {result:+.2f} EUR nach Gebühren")
            return
        del self.positions[symbol]
        self.last_exit_ts = now
        self._say(f"VERKAUF {symbol} @ {fill.price:.2f} ({label}), Ergebnis {result:+.2f} EUR nach Gebühren")

    def _uncertain(self, err: Exception) -> None:
        self.lock = str(err)
        self._say(f"ACHTUNG: {err}")

    def _say(self, msg: str, log: bool = True) -> None:
        """Ereignis melden. Gleiche Meldung direkt hintereinander landet nur einmal im Log."""
        line = time.strftime("%d.%m. %H:%M:%S ") + msg
        if log and msg != self._last_msg:
            self.log = (self.log + [line])[-200:]
            self._last_msg = msg
        if self.on_log:
            self.on_log(line)

    def start_day(self, day: str, equity: float) -> None:
        """Neuer Kalendertag -> Tageslimit bezieht sich auf das Kapital von heute früh."""
        if day == self.day:
            return
        if self.day:  # nicht beim allerersten Aufruf
            self.risk = RiskState(day_start_equity=equity)
            self._say(f"Neuer Tag, Tageslimit zurückgesetzt (Kapital {equity:.2f})")
        self.day = day

    def kill_switch_on(self) -> bool:
        return Path(self.cfg.kill_file).exists()

    def tick(self, price: float, news: float | None, now: float | None = None,
             fear_greed: int | None = None) -> None:
        now = time.time() if now is None else now
        cutoff = now - self.strat_cfg.trend_window_s
        self.history = [(t, p) for t, p in self.history if t >= cutoff] + [(now, price)]
        symbol = self.cfg.symbol
        equity = self.broker.equity({symbol: price})
        was_halted = self.risk.halted
        check_daily_limit(self.risk, equity, self.risk_cfg)
        if self.risk.halted and not was_halted:
            self._say(f"Keine neuen Käufe mehr heute: {self.risk.reason}")
        kill = self.kill_switch_on()

        # 1) Bestehende Position absichern
        pos = self.positions.get(symbol)
        if pos:
            try:
                done = self.broker.sync(symbol)      # hat die Börse den Stop inzwischen ausgelöst?
                if done:
                    self._close(pos, done, "exchange", now)
                    pos = self.positions.get(symbol)
                if pos:
                    reason = update_position(pos, price, self.risk_cfg)
                    if kill:
                        reason = reason or "kill_switch"
                    if reason:
                        self._close(pos, self.broker.sell(symbol, pos.qty, price), reason, now)
                    else:
                        self.broker.set_stop(symbol, pos.stop)   # anlegen bzw. nach oben nachziehen
            except BrokerUncertain as err:
                self._uncertain(err)
            except BrokerError as err:
                self._say(f"Börse meldet Fehler ({err}), neuer Versuch beim nächsten Kurs")

        # 2) Neue Position nur, wenn nichts dagegen spricht
        blocked = None
        if self.lock:
            blocked = f"Gesperrt bis zum Neustart: {self.lock}"
        elif self.risk.halted:
            blocked = f"Gestoppt: {self.risk.reason}"
        elif kill:
            blocked = "Not-Aus aktiv"
        elif symbol in self.positions or len(self.positions) >= self.risk_cfg.max_open_positions:
            blocked = "Position offen, kein weiterer Kauf"
        if blocked:
            self.signal = [Check(False, blocked)]
            return
        sig = entry_signal(self.history, price, news, fear_greed, now, self.last_exit_ts, self.strat_cfg)
        self.signal = sig.checks
        if not sig.buy:
            return
        qty = position_size(equity, price, self.risk_cfg)
        qty = min(qty, self.broker.cash / (price * (1 + self.risk_cfg.fee_pct)))
        if qty * price < 1:
            return
        try:
            fill = self.broker.buy(symbol, qty, price)
        except BrokerUncertain as err:
            self._uncertain(err)
            return
        except BrokerError as err:
            self._say(f"Kauf abgelehnt: {err}")
            return
        pos = self.positions[symbol] = open_position(symbol, fill.qty, fill.price, self.risk_cfg, cost=fill.total)
        self.signal = [Check(False, "Position offen, kein weiterer Kauf")]
        self._say(f"KAUF {symbol} {fill.qty:.6f} @ {fill.price:.2f} (= {fill.qty * fill.price:.2f} EUR), "
                  f"Stop {pos.stop:.2f}, Ziel {pos.take_profit:.2f}")
        try:
            self.broker.set_stop(symbol, pos.stop)
        except BrokerUncertain as err:
            self._uncertain(err)
        except BrokerError as err:
            self._say(f"ACHTUNG: Stop an der Börse noch nicht gesetzt ({err}), neuer Versuch beim nächsten Kurs")

    def snapshot(self, price: float) -> dict:
        return {
            "saved_at": time.time(),
            "equity": round(self.broker.equity({self.cfg.symbol: price}), 2),
            "cash": round(self.broker.cash, 2),
            "start_equity": self.start_equity,
            "halted": self.risk.halted,
            "halt_reason": self.risk.reason,
            "lock": self.lock,
            "kill_switch": self.kill_switch_on(),
            "status": self.status,
            "signal": [asdict(c) for c in self.signal],
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "trades": self.broker.trades[-50:],
            "log": self.log[-50:],
            # Exakte Werte, um nach einem Neustart genau dort weiterzumachen
            "resume": {
                "cash": self.broker.cash,
                "start_equity": self.start_equity,
                "holdings": dict(self.broker.holdings),
                "stops": dict(self.broker.stops),
                "positions": {s: asdict(p) for s, p in self.positions.items()},
                "trades": self.broker.trades[-500:],
                "history": [list(x) for x in self.history],
                "last_exit_ts": self.last_exit_ts,
                "day": self.day,
                "day_start_equity": self.risk.day_start_equity,
                "halted": self.risk.halted,
                "halt_reason": self.risk.reason,
            },
        }

    def restore(self) -> bool:
        """Lädt den letzten Stand aus state_file. False, wenn nichts Brauchbares da ist."""
        try:
            with open(self.cfg.state_file, encoding="utf-8") as f:
                r = json.load(f)["resume"]
            cash, start = float(r["cash"]), float(r["start_equity"])
            holdings = {str(k): float(v) for k, v in r["holdings"].items()}
            positions = {str(k): Position(**v) for k, v in r["positions"].items()}
            trades = list(r["trades"])
            stops = {str(k): dict(v) for k, v in r.get("stops", {}).items()}
            history = [(float(t), float(p)) for t, p in r["history"]]
            last_exit = float(r["last_exit_ts"])
            risk = RiskState(day_start_equity=float(r["day_start_equity"]),
                             halted=bool(r["halted"]), reason=str(r["halt_reason"]))
            day = str(r["day"])
        except (OSError, KeyError, ValueError, TypeError):
            return False
        if cash < 0 or start <= 0 or risk.day_start_equity <= 0 or set(holdings) != set(positions):
            return False
        self.broker.cash, self.broker.holdings, self.broker.trades = cash, holdings, trades
        self.broker.stops = stops
        self.positions, self.history, self.risk, self.day = positions, history, risk, day
        self.start_equity, self.last_exit_ts = start, last_exit
        return True

    def save(self, price: float) -> None:
        tmp = self.cfg.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(price), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.cfg.state_file)
