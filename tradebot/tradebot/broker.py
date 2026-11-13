"""Broker-Schnittstelle. Jeder Broker bietet:

  buy(symbol, qty, price)  -> Fill      kaufen
  sell(symbol, qty, price) -> Fill      verkaufen (vorher den Stop an der Börse aufräumen)
  set_stop(symbol, price)               Stop-Loss an der Börse setzen bzw. nach oben nachziehen
  sync(symbol)             -> Fill|None hat die Börse den Stop inzwischen ausgelöst?
  equity(prices), cash, holdings, trades, stops, min_qty

PaperBroker simuliert das Ganze ohne Börse; der Stop wird dort nur von der Engine geprüft."""
from __future__ import annotations

from dataclasses import dataclass, field


class BrokerError(RuntimeError):
    pass


class BrokerUncertain(BrokerError):
    """Der Zustand an der Börse ist unklar (z. B. Zeitüberschreitung mitten in einer Order).
    Die Engine kauft dann nichts mehr, bis ein Mensch nachgesehen und den Bot neu gestartet hat."""


@dataclass
class Fill:
    qty: float
    price: float   # Durchschnittskurs
    fee: float     # in EUR

    @property
    def net(self) -> float:
        """Erlös eines Verkaufs nach Gebühr."""
        return self.qty * self.price - self.fee

    @property
    def total(self) -> float:
        """Kosten eines Kaufs inklusive Gebühr."""
        return self.qty * self.price + self.fee


@dataclass
class PaperBroker:
    cash: float
    fee_pct: float = 0.008
    holdings: dict[str, float] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    stops: dict[str, dict] = field(default_factory=dict)   # bleibt leer, nur für die gemeinsame Schnittstelle
    min_qty: float = 1e-9

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices.get(s, 0.0) for s, q in self.holdings.items())

    def buy(self, symbol: str, qty: float, price: float) -> Fill:
        fill = Fill(qty, price, qty * price * self.fee_pct)
        if fill.total > self.cash + 1e-9:
            raise BrokerError("Nicht genug Cash")
        self.cash -= fill.total
        self.holdings[symbol] = self.holdings.get(symbol, 0.0) + qty
        self.trades.append({"side": "buy", "symbol": symbol, "qty": qty, "price": price, "fee": fill.fee})
        return fill

    def sell(self, symbol: str, qty: float, price: float) -> Fill:
        held = self.holdings.get(symbol, 0.0)
        qty = min(qty, held)
        if qty <= 0:
            raise BrokerError("Keine Position zum Verkaufen")
        fill = Fill(qty, price, qty * price * self.fee_pct)
        self.cash += fill.net
        self.holdings[symbol] = held - qty
        if self.holdings[symbol] <= 1e-12:
            del self.holdings[symbol]
        self.trades.append({"side": "sell", "symbol": symbol, "qty": qty, "price": price, "fee": fill.fee})
        return fill

    def set_stop(self, symbol: str, stop_price: float) -> None:
        pass

    def sync(self, symbol: str) -> Fill | None:
        return None
