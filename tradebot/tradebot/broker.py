"""Broker-Abstraktion. PaperBroker simuliert Fills; Live-Broker muss dieselbe Schnittstelle erfuellen."""
from __future__ import annotations

from dataclasses import dataclass, field


class BrokerError(RuntimeError):
    pass


@dataclass
class PaperBroker:
    cash: float
    fee_pct: float = 0.015
    holdings: dict[str, float] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices.get(s, 0.0) for s, q in self.holdings.items())

    def buy(self, symbol: str, qty: float, price: float) -> float:
        cost = qty * price
        fee = cost * self.fee_pct
        if cost + fee > self.cash + 1e-9:
            raise BrokerError("Nicht genug Cash")
        self.cash -= cost + fee
        self.holdings[symbol] = self.holdings.get(symbol, 0.0) + qty
        self.trades.append({"side": "buy", "symbol": symbol, "qty": qty, "price": price, "fee": fee})
        return qty

    def sell(self, symbol: str, qty: float, price: float) -> float:
        held = self.holdings.get(symbol, 0.0)
        qty = min(qty, held)
        if qty <= 0:
            raise BrokerError("Keine Position zum Verkaufen")
        proceeds = qty * price
        fee = proceeds * self.fee_pct
        self.cash += proceeds - fee
        self.holdings[symbol] = held - qty
        if self.holdings[symbol] <= 1e-12:
            del self.holdings[symbol]
        self.trades.append({"side": "sell", "symbol": symbol, "qty": qty, "price": price, "fee": fee})
        return proceeds - fee


class BitpandaBroker:
    """Platzhalter. Live-Handel ist absichtlich nicht implementiert, bis die
    Bitpanda-API-Doku geprueft und ein Sandbox-/Testzugang getestet ist."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Bitpanda-Anbindung ist noch nicht implementiert (API-Doku nicht verifiziert)."
        )
