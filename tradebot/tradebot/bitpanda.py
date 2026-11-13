"""Bitpanda Public API (https://docs.public.bitpanda.com, Stand der Doku: Okt 2026).

Wichtig (aus der Doku):
- Auth: Header `x-api-key`. Basis-URL: https://api.public.bitpanda.com
- Handel NUR ueber Quotes: POST /v1/quotes (BUY/SELL + quantity oder notional), dann
  POST /v1/quotes/{id}/accept. Es gibt KEINE Limit-/Stop-/Trailing-Orders.
  => Stop-Loss und Trailing-Stop muessen im Bot laufen. Faellt der Bot aus, ist eine
  offene Position UNGESCHUETZT.
- Eigene Tageslimits pro API-Key: GET/POST /v1/trading-limits. Der Broker verweigert den
  Start ohne gesetztes Limit.
- Rate-Limit Trading: 5 Anfragen/Sekunde.

Dieser Code wurde nur gegen simulierte Antworten getestet, NICHT gegen das echte Konto."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import ROUND_DOWN, Decimal
from typing import Callable

from .broker import BrokerError

BASE_URL = "https://api.public.bitpanda.com"
Transport = Callable[[str, str, dict | None], dict]


def make_transport(api_key: str, timeout: float = 15.0) -> Transport:
    def call(method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            BASE_URL + path, data=data, method=method,
            headers={"x-api-key": api_key, "Content-Type": "application/json",
                     "User-Agent": "tradebot/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise BrokerError(f"Bitpanda HTTP {e.code} bei {method} {path}") from e
    return call


def _qty_str(qty: float) -> str:
    q = Decimal(str(qty)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    if q <= 0:
        raise BrokerError("Menge zu klein")
    return format(q, "f")


class BitpandaBroker:
    """Gleiche Schnittstelle wie PaperBroker. `cash` ist das vom Bot verwaltete Kapital
    (z. B. 100 EUR), nicht das gesamte Konto."""

    def __init__(self, transport: Transport, symbols: dict[str, str], start_cash: float,
                 live: bool = False, max_buy_slippage: float = 0.02,
                 max_sell_slippage: float = 0.05, currency_symbol: str = "EUR"):
        if not live:
            raise BrokerError("Live-Handel nicht freigegeben (live=True fehlt)")
        self.t = transport
        self.cash = start_cash
        self.holdings: dict[str, float] = {}
        self.trades: list[dict] = []
        self.max_buy_slippage = max_buy_slippage
        self.max_sell_slippage = max_sell_slippage
        self._require_trading_limit()
        self.currency_id = self._lookup_currency(currency_symbol)
        self.asset_ids = {s: self._lookup_asset(base) for s, base in symbols.items()}

    # --- Setup / Sicherheit ---
    def _require_trading_limit(self) -> None:
        try:
            d = self.t("GET", "/v1/trading-limits", None)["data"]
        except BrokerError as e:
            raise BrokerError(
                "Kein Tageslimit am API-Key gesetzt (oder nicht abrufbar). "
                "Bitte zuerst ein kleines Tageslimit setzen. Start verweigert.") from e
        if not d.get("buy_limit"):
            raise BrokerError("Tageslimit ist 0/leer. Start verweigert.")

    def _lookup_currency(self, symbol: str) -> str:
        for c in self.t("GET", "/v1/currencies", None)["data"]:
            if c.get("symbol") == symbol:
                return c["id"]
        raise BrokerError(f"Waehrung {symbol} nicht gefunden")

    def _lookup_asset(self, symbol: str) -> str:
        data = self.t("GET", f"/v1/assets?symbol={symbol}&type=CRYPTO", None)["data"]
        match = [a for a in data if a.get("symbol") == symbol and a.get("buy_active") and a.get("sell_active")]
        if len(match) != 1:
            raise BrokerError(f"Asset {symbol}: {len(match)} Treffer (erwartet genau 1)")
        return match[0]["id"]

    # --- Schnittstelle wie PaperBroker ---
    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices.get(s, 0.0) for s, q in self.holdings.items())

    def _trade(self, side: str, symbol: str, qty: float, ref_price: float) -> dict:
        quote = self.t("POST", "/v1/quotes", {
            "asset_id": self.asset_ids[symbol], "currency_id": self.currency_id,
            "side": side, "quantity": _qty_str(qty)})["data"]
        q = quote["quote"]
        price = float(q["price_with_fee"])
        if side == "BUY" and price > ref_price * (1 + self.max_buy_slippage):
            raise BrokerError(f"Quote {price} zu teuer gegenueber {ref_price}")
        if side == "SELL" and price < ref_price * (1 - self.max_sell_slippage):
            raise BrokerError(f"Quote {price} zu niedrig gegenueber {ref_price}")
        acc = self.t("POST", f"/v1/quotes/{quote['quote_id']}/accept", None)["data"]
        return acc["execution"]

    def buy(self, symbol: str, qty: float, price: float) -> float:
        if qty * price * 1.05 > self.cash:  # grobe Vorpruefung inkl. Puffer fuer Gebuehr
            raise BrokerError("Nicht genug Bot-Kapital")
        ex = self._trade("BUY", symbol, qty, price)
        got, px = float(ex["quantity"]), float(ex["price_with_fee"])
        self.cash -= got * px
        self.holdings[symbol] = self.holdings.get(symbol, 0.0) + got
        self.trades.append({"side": "buy", "symbol": symbol, "qty": got, "price": px, "fee": float(ex.get("fee", 0))})
        return got

    def sell(self, symbol: str, qty: float, price: float) -> float:
        qty = min(qty, self.holdings.get(symbol, 0.0))
        if qty <= 0:
            raise BrokerError("Keine Position zum Verkaufen")
        ex = self._trade("SELL", symbol, qty, price)
        got, px = float(ex["quantity"]), float(ex["price_with_fee"])
        self.cash += got * px
        self.holdings[symbol] -= got
        if self.holdings[symbol] <= 1e-9:
            del self.holdings[symbol]
        self.trades.append({"side": "sell", "symbol": symbol, "qty": got, "price": px, "fee": float(ex.get("fee", 0))})
        return got * px
