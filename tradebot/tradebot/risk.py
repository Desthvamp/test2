"""Risikomanagement: Positionsgröße, Stop-Loss, Take-Profit, Trailing-Stop, Tageslimit, Kill-Switch."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskConfig:
    stop_loss_pct: float = 0.0         # 0 = KEIN Stop-Loss: Position wird gehalten, nie mit Verlust verkauft.
                                       # z. B. 0.05 = Verkauf bei -5 % unter dem Einstieg
    position_pct: float = 0.20         # ohne Stop-Loss: Anteil des Kapitals pro Kauf (und pro Nachkauf)
    risk_per_trade_pct: float = 0.02   # nur mit Stop-Loss: max. Verlust pro Kauf (inkl. Gebühren)
    max_position_pct: float = 0.50     # max. Anteil des Kapitals pro Kauf
    take_profit_pct: float = 0.10      # Gewinnmitnahme
    trail_activation_pct: float = 0.06 # ab diesem Gewinn wird Trailing-Stop aktiv (deckt dann etwa die Gebühren)
    trail_distance_pct: float = 0.03   # Abstand des Trailing-Stops zum Hoch
    max_daily_loss_pct: float = 0.05   # Tagesverlust, ab dem nichts mehr eroeffnet wird
    max_open_positions: int = 1
    max_adds: int = 3                  # so oft darf pro Position nachgekauft werden (0 = aus)
    add_retrace: float = 0.3           # Nachkauf, sobald sich der Kurs vom Tief um diesen Anteil des Rückgangs erholt hat
    add_min_discount_fees: float = 1.0 # ... und noch mindestens so viele Hin-und-zurück-Gebühren unter dem letzten Kauf liegt
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
    last_buy: float = 0.0   # Kurs des letzten Kaufs (Einstieg oder Nachkauf)
    low: float = 0.0        # tiefster Kurs, seit der Kurs zuletzt beim letzten Kauf oder darüber stand


@dataclass
class RiskState:
    day_start_equity: float
    halted: bool = False
    reason: str = ""


def position_size(equity: float, price: float, cfg: RiskConfig) -> float:
    """Ohne Stop-Loss: fester Anteil des Kapitals. Mit Stop-Loss: Menge so, dass ein Stop-Treffer
    inklusive Kauf- und Verkaufsgebühr höchstens risk_per_trade_pct des Kapitals kostet."""
    if price <= 0 or equity <= 0:
        return 0.0
    if cfg.stop_loss_pct > 0:
        loss_per_euro = cfg.stop_loss_pct + 2 * cfg.fee_pct
        qty_by_risk = equity * cfg.risk_per_trade_pct / (price * loss_per_euro)
    else:
        qty_by_risk = equity * cfg.position_pct / price
    qty_by_cap = equity * cfg.max_position_pct / price
    return max(0.0, min(qty_by_risk, qty_by_cap))


def initial_stop(entry: float, cfg: RiskConfig) -> float:
    """0.0 bedeutet: kein Stop. Erst der nachgezogene Stop im Gewinn setzt dann einen."""
    return entry * (1 - cfg.stop_loss_pct) if cfg.stop_loss_pct > 0 else 0.0


def open_position(symbol: str, qty: float, price: float, cfg: RiskConfig, cost: float = 0.0) -> Position:
    return Position(
        cost=cost or qty * price * (1 + cfg.fee_pct),
        symbol=symbol,
        qty=qty,
        entry=price,
        peak=price,
        stop=initial_stop(price, cfg),
        take_profit=price * (1 + cfg.take_profit_pct),
        last_buy=price,
        low=price,
    )


def add_to_position(pos: Position, qty: float, price: float, cost: float, cfg: RiskConfig) -> None:
    """Nachkauf verbuchen: Durchschnittskurs neu, Ziel (und ein Stop-Loss, falls eingestellt) beziehen
    sich danach auf diesen Schnitt. Ein Stop rutscht dadurch nach unten; nur hier passiert das."""
    pos.entry = (pos.qty * pos.entry + qty * price) / (pos.qty + qty)
    pos.qty += qty
    pos.cost += cost
    pos.adds += 1
    pos.last_buy = pos.low = price
    pos.stop = initial_stop(pos.entry, cfg)
    pos.take_profit = pos.entry * (1 + cfg.take_profit_pct)


def add_zone(pos: Position, cfg: RiskConfig) -> tuple[float, float]:
    """Kursbereich (von, bis), in dem nachgekauft wird. Ist von > bis, war der Rückgang noch zu klein.

    von: Tief + add_retrace x Rückgang. Erst dann gilt die Erholung als erkennbar. Das passt sich
         an die Bewegung an: kleiner Rückgang -> kleine Erholung reicht, großer -> es braucht mehr.
    bis: letzter Kauf abzüglich der Gebühren für Kauf und Verkauf. Darüber lohnt ein Nachkauf nicht.

    Eine Regel, die den Abstand an die Schwankung der letzten Stunden koppelt, wurde an echten Kursen
    (November 2026) ausprobiert und verworfen: Sie blockierte Nachkäufe genau in den Umkehrpunkten."""
    lo = pos.low if pos.low > 0 else pos.last_buy
    start = lo + cfg.add_retrace * (pos.last_buy - lo)
    end = pos.last_buy * (1 - 2 * cfg.fee_pct * cfg.add_min_discount_fees)
    return start, end


def update_position(pos: Position, price: float, cfg: RiskConfig) -> str | None:
    """Aktualisiert Hoch/Trailing-Stop. Gibt 'stop', 'take_profit' oder None zurueck."""
    pos.peak = max(pos.peak, price)
    if pos.last_buy <= 0:              # Stand aus einer älteren Version
        pos.last_buy = pos.entry
    # Tief des laufenden Rückgangs; steht der Kurs wieder beim letzten Kauf, beginnt die Messung neu
    pos.low = price if price >= pos.last_buy or pos.low <= 0 else min(pos.low, price)
    if not pos.trailing_active and price >= pos.entry * (1 + cfg.trail_activation_pct):
        pos.trailing_active = True
    if pos.trailing_active:
        # Stop wandert nur nach oben und sichert Gewinne ab.
        pos.stop = max(pos.stop, pos.peak * (1 - cfg.trail_distance_pct))
    if pos.stop > 0 and price <= pos.stop:
        return "stop"
    if price >= pos.take_profit:
        return "take_profit"
    return None


def check_daily_limit(state: RiskState, equity: float, cfg: RiskConfig) -> None:
    loss = (state.day_start_equity - equity) / state.day_start_equity
    if loss >= cfg.max_daily_loss_pct and not state.halted:
        state.halted = True
        state.reason = f"Tagesverlust {loss:.1%} erreicht"
