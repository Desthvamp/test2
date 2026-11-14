"""Risikomanagement: Positionsgröße, Stop-Loss, Take-Profit, Trailing-Stop, Tageslimit, Kill-Switch."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.02   # max. Verlust pro Trade (inkl. Gebühren) in % des Kapitals
    max_position_pct: float = 0.50     # max. Anteil des Kapitals in einer Position
    stop_loss_pct: float = 0.05        # initialer Stop-Loss
    take_profit_pct: float = 0.10      # Gewinnmitnahme
    trail_activation_pct: float = 0.06 # ab diesem Gewinn wird Trailing-Stop aktiv (deckt dann etwa die Gebühren)
    trail_distance_pct: float = 0.03   # Abstand des Trailing-Stops zum Hoch
    max_daily_loss_pct: float = 0.05   # Tagesverlust, ab dem nichts mehr eroeffnet wird
    max_open_positions: int = 1
    max_adds: int = 1                  # so oft darf pro Position nachgekauft werden (0 = aus)
    add_drop_pct: float = 0.03         # Nachkauf erst, wenn der Kurs so weit unter dem Einstieg liegt
    fee_pct: float = 0.008             # Gebühr je Kauf/Verkauf: Kraken Taker, niedrigster Tarif


@dataclass
class Position:
    symbol: str
    qty: float
    entry: float
    peak: float
    stop: float
    take_profit: float
    trailing_active: bool = False
    cost: float = 0.0   # bezahlter Betrag inklusive Kaufgebühr
    adds: int = 0       # Zahl der Nachkäufe; `entry` ist dann der Durchschnittskurs


@dataclass
class RiskState:
    day_start_equity: float
    halted: bool = False
    reason: str = ""


def position_size(equity: float, price: float, cfg: RiskConfig) -> float:
    """Menge so wählen, dass ein Stop-Loss-Treffer inklusive Kauf- und Verkaufsgebuehr
    höchstens risk_per_trade_pct des Kapitals kostet."""
    if price <= 0 or equity <= 0:
        return 0.0
    risk_amount = equity * cfg.risk_per_trade_pct
    loss_per_euro = cfg.stop_loss_pct + 2 * cfg.fee_pct
    qty_by_risk = risk_amount / (price * loss_per_euro)
    qty_by_cap = equity * cfg.max_position_pct / price
    return max(0.0, min(qty_by_risk, qty_by_cap))


def open_position(symbol: str, qty: float, price: float, cfg: RiskConfig, cost: float = 0.0) -> Position:
    return Position(
        cost=cost or qty * price * (1 + cfg.fee_pct),
        symbol=symbol,
        qty=qty,
        entry=price,
        peak=price,
        stop=price * (1 - cfg.stop_loss_pct),
        take_profit=price * (1 + cfg.take_profit_pct),
    )


def add_to_position(pos: Position, qty: float, price: float, cost: float, cfg: RiskConfig) -> None:
    """Nachkauf verbuchen: Durchschnittskurs neu, Stop und Ziel beziehen sich danach auf diesen Schnitt.
    Der Stop rutscht dadurch nach unten; das ist die einzige Stelle, an der das passiert."""
    pos.entry = (pos.qty * pos.entry + qty * price) / (pos.qty + qty)
    pos.qty += qty
    pos.cost += cost
    pos.adds += 1
    pos.stop = pos.entry * (1 - cfg.stop_loss_pct)
    pos.take_profit = pos.entry * (1 + cfg.take_profit_pct)


def update_position(pos: Position, price: float, cfg: RiskConfig) -> str | None:
    """Aktualisiert Hoch/Trailing-Stop. Gibt 'stop', 'take_profit' oder None zurueck."""
    pos.peak = max(pos.peak, price)
    if not pos.trailing_active and price >= pos.entry * (1 + cfg.trail_activation_pct):
        pos.trailing_active = True
    if pos.trailing_active:
        # Stop wandert nur nach oben und sichert Gewinne ab.
        pos.stop = max(pos.stop, pos.peak * (1 - cfg.trail_distance_pct))
    if price <= pos.stop:
        return "stop"
    if price >= pos.take_profit:
        return "take_profit"
    return None


def check_daily_limit(state: RiskState, equity: float, cfg: RiskConfig) -> None:
    loss = (state.day_start_equity - equity) / state.day_start_equity
    if loss >= cfg.max_daily_loss_pct and not state.halted:
        state.halted = True
        state.reason = f"Tagesverlust {loss:.1%} erreicht"
