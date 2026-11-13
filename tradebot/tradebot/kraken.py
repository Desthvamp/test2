"""Kraken Spot REST (https://docs.kraken.com, gelesen im November 2026).

Aus der Doku:
- Privat-Aufrufe: Header API-Key und API-Sign. API-Sign = HMAC-SHA512 über
  (URI-Pfad + SHA256(nonce + POST-Daten)) mit dem base64-dekodierten Secret. `nonce` muss steigen.
- AddOrder kennt u. a. market, limit, stop-loss, trailing-stop; `timeinforce=IOC`; `validate=true`
  prüft eine Order nur, ohne zu handeln. AmendOrder ändert `trigger_price` einer Stop-Order.
- Antworten haben immer `error` (Liste) und `result`. HTTP-Status sagt nichts über den Erfolg.
- Gebühr im niedrigsten Tarif (Spot): 0,40 % Maker, 0,80 % Taker. Der Bot handelt als Taker.
- Nötige Rechte am API-Key: Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades,
  Create & Modify Orders, Cancel/Close Orders. KEIN Withdraw.

So arbeitet der Broker:
- Kauf als Limit-Order mit IOC knapp über dem aktuellen Kurs: wird sofort ausgeführt oder gar nicht.
- Danach liegt der Stop-Loss als echte Order bei Kraken und wird dort nur nach oben nachgezogen.
  Er greift also auch, wenn der Bot oder das Gerät ausfällt.
- Gewinnmitnahme und Not-Aus macht der Bot selbst: Stop-Order löschen, dann Markt-Verkauf.

Die Signatur ist gegen das Beispiel aus der Doku geprüft, der Rest gegen eine nachgebaute Börse
in den Tests. Gegen ein echtes Konto ist NICHTS davon gelaufen."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, Decimal
from typing import Callable

from .broker import BrokerError, BrokerUncertain, Fill

API = "https://api.kraken.com"
Http = Callable[[str, bytes | None, dict], bytes]
DONE = ("closed", "canceled", "expired")


def sign(urlpath: str, data: dict, secret: str) -> str:
    encoded = (str(data["nonce"]) + urllib.parse.urlencode(data)).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class TransportError(Exception):
    """Die Anfrage hat Kraken nicht (sicher) erreicht oder die Antwort kam nicht an."""


def _http(url: str, body: bytes | None, headers: dict) -> bytes:
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "tradebot/0.1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return r.read()
    except urllib.error.HTTPError as e:
        raise TransportError(f"HTTP {e.code}") from e
    except OSError as e:   # URLError, Timeout, Verbindungsabbruch
        raise TransportError(str(e)) from e


class KrakenClient:
    def __init__(self, key: str = "", secret: str = "", http: Http = _http,
                 clock: Callable[[], float] = time.time):
        self.key, self.secret, self.http, self.clock = key, secret, http, clock
        self._nonce = 0

    @staticmethod
    def _result(raw: bytes):
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise TransportError("Antwort ist kein JSON") from e
        errors = [x for x in d.get("error", []) if str(x).startswith("E")]
        if errors:
            raise BrokerError("Kraken: " + ", ".join(errors))
        return d.get("result", {})

    def public(self, method: str, **params):
        url = f"{API}/0/public/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return self._result(self.http(url, None, {}))
        except TransportError as e:
            raise BrokerError(f"Kraken nicht erreichbar: {e}") from e

    def private(self, method: str, **data):
        """Wirft TransportError, wenn unklar ist, ob die Anfrage ankam."""
        if not self.key or not self.secret:
            raise BrokerError("Kraken-API-Key fehlt")
        self._nonce = max(self._nonce + 1, int(self.clock() * 1000))
        path = f"/0/private/{method}"
        payload = {"nonce": self._nonce, **data}
        headers = {"API-Key": self.key, "API-Sign": sign(path, payload, self.secret),
                   "Content-Type": "application/x-www-form-urlencoded"}
        return self._result(self.http(API + path, urllib.parse.urlencode(payload).encode(), headers))

    def read(self, method: str, **data):
        """Privater Lese-Aufruf: ein Fehlschlag ist harmlos, also normaler BrokerError."""
        try:
            return self.private(method, **data)
        except TransportError as e:
            raise BrokerError(f"Kraken nicht erreichbar: {e}") from e


class KrakenBroker:
    """Gleiche Schnittstelle wie PaperBroker. `cash` ist das vom Bot verwaltete Kapital
    (z. B. 100 EUR), nicht das ganze Konto."""

    def __init__(self, client: KrakenClient, start_cash: float, live: bool = False,
                 symbol: str = "BTC-EUR", pair: str = "XBTEUR", max_order_eur: float | None = None,
                 max_buy_slippage: float = 0.005, min_stop_step: float = 0.002,
                 sleep: Callable[[float], None] = time.sleep, poll_tries: int = 10, poll_wait: float = 0.5):
        if not live:
            raise BrokerError("Live-Handel nicht freigegeben (live=True fehlt)")
        self.c, self.symbol, self.pair = client, symbol, pair
        self.cash = start_cash
        self.holdings: dict[str, float] = {}
        self.trades: list[dict] = []
        self.stops: dict[str, dict] = {}        # symbol -> {"txid": ..., "price": ...}
        self.max_order_eur = start_cash if max_order_eur is None else max_order_eur
        self.max_buy_slippage, self.min_stop_step = max_buy_slippage, min_stop_step
        self.sleep, self.poll_tries, self.poll_wait = sleep, poll_tries, poll_wait

        info = next(iter(client.public("AssetPairs", pair=pair).values()))
        if info.get("status") != "online":
            raise BrokerError(f"Handelspaar {pair} ist nicht handelbar (Status {info.get('status')})")
        self.base, self.quote = info["base"], info["quote"]
        self.min_qty = float(info["ordermin"])
        self.price_decimals, self.qty_decimals = int(info["pair_decimals"]), int(info["lot_decimals"])

        available = float(client.read("Balance").get(self.quote, 0))
        if available < start_cash:
            raise BrokerError(f"Auf dem Kraken-Konto liegen nur {available:.2f} EUR, "
                              f"der Bot soll aber {start_cash:.2f} EUR verwalten")
        self.fee_pct = 0.008
        try:
            fees = client.read("TradeVolume", pair=pair).get("fees", {})
            self.fee_pct = float(next(iter(fees.values()))["fee"]) / 100
        except (BrokerError, StopIteration, KeyError, ValueError):
            pass   # Standardwert bleibt; die echte Gebühr steht ohnehin in jeder Ausführung

    # --- Hilfen ---
    def _p(self, price: float) -> str:
        return f"{price:.{self.price_decimals}f}"

    def _q(self, qty: float) -> str:
        step = Decimal(1).scaleb(-self.qty_decimals)
        return format(Decimal(str(qty)).quantize(step, rounding=ROUND_DOWN), "f")

    def _order(self, what: str, **params) -> str:
        """Gibt die Order auf. Bei unklarem Ausgang BrokerUncertain."""
        try:
            return self.c.private("AddOrder", pair=self.pair, oflags="fciq", **params)["txid"][0]
        except TransportError as e:
            raise BrokerUncertain(f"{what}: unklar, ob die Order bei Kraken ankam ({e}). "
                                  "Bitte Kraken-Konto prüfen, dann Bot neu starten.") from e

    def _query(self, txid: str) -> dict | None:
        return self.c.read("QueryOrders", txid=txid).get(txid)

    def _cancel(self, txid: str) -> None:
        try:
            self.c.read("CancelOrder", txid=txid)
        except BrokerError:
            pass   # z. B. schon ausgeführt; der Status wird danach ohnehin abgefragt

    def _wait_done(self, txid: str) -> dict:
        order = None
        for i in range(self.poll_tries):
            order = self._query(txid)
            if order and order.get("status") in DONE:
                return order
            self.sleep(self.poll_wait)
        self._cancel(txid)
        order = self._query(txid)
        if not order or order.get("status") not in DONE:
            raise BrokerUncertain(f"Order {txid} hängt bei Kraken. Bitte Konto prüfen, dann Bot neu starten.")
        return order

    @staticmethod
    def _fill(order: dict) -> Fill | None:
        qty = float(order.get("vol_exec", 0))
        if qty <= 0:
            return None
        return Fill(qty, float(order["cost"]) / qty, float(order["fee"]))

    def _book_sell(self, symbol: str, fill: Fill) -> None:
        self.cash += fill.net
        left = self.holdings.get(symbol, 0.0) - fill.qty
        if left < self.min_qty:     # Rest unter der Mindestmenge ist nicht handelbar
            self.holdings.pop(symbol, None)
        else:
            self.holdings[symbol] = left
        self.trades.append({"side": "sell", "symbol": symbol, "qty": fill.qty, "price": fill.price, "fee": fill.fee})

    def _settle_stop(self, symbol: str, cancel: bool) -> Fill | None:
        """Fragt die Stop-Order ab (und löscht sie bei cancel=True). Verbucht eine Ausführung."""
        cur = self.stops.get(symbol)
        if not cur:
            return None
        if cancel:
            self._cancel(cur["txid"])
        order = self._query(cur["txid"])
        if not order or order.get("status") not in DONE:
            if cancel:
                raise BrokerUncertain(f"Stop-Order {cur['txid']} ließ sich nicht löschen. "
                                      "Bitte Konto prüfen, dann Bot neu starten.")
            return None
        del self.stops[symbol]
        fill = self._fill(order)
        if fill:
            self._book_sell(symbol, fill)
        return fill

    # --- Schnittstelle ---
    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices.get(s, 0.0) for s, q in self.holdings.items())

    def buy(self, symbol: str, qty: float, price: float) -> Fill:
        volume = self._q(qty)
        if float(volume) < self.min_qty:
            raise BrokerError(f"Menge {volume} liegt unter Krakens Mindestmenge {self.min_qty}")
        limit = price * (1 + self.max_buy_slippage)
        worst = float(volume) * limit * (1 + self.fee_pct)
        if worst > self.cash + 1e-9:
            raise BrokerError("Nicht genug Bot-Kapital")
        if float(volume) * limit > self.max_order_eur + 1e-9:
            raise BrokerError(f"Order über {float(volume) * limit:.2f} EUR übersteigt die Obergrenze "
                              f"von {self.max_order_eur:.2f} EUR")
        txid = self._order("Kauf", type="buy", ordertype="limit", price=self._p(limit),
                           volume=volume, timeinforce="IOC")
        fill = self._fill(self._wait_done(txid))
        if not fill:
            raise BrokerError("Kauf nicht ausgeführt, der Kurs ist weggelaufen")
        self.cash -= fill.total
        self.trades.append({"side": "buy", "symbol": symbol, "qty": fill.qty, "price": fill.price, "fee": fill.fee})
        if fill.qty < self.min_qty:
            raise BrokerError(f"Nur {fill.qty} gekauft, das ist unter der Mindestmenge und bleibt als Rest liegen")
        self.holdings[symbol] = self.holdings.get(symbol, 0.0) + fill.qty
        return fill

    def set_stop(self, symbol: str, stop_price: float) -> None:
        held = self.holdings.get(symbol, 0.0)
        if held < self.min_qty:
            return
        cur = self.stops.get(symbol)
        if cur is None:
            txid = self._order("Stop-Loss", type="sell", ordertype="stop-loss",
                               price=self._p(stop_price), volume=self._q(held))
            self.stops[symbol] = {"txid": txid, "price": float(self._p(stop_price))}
        elif stop_price >= cur["price"] * (1 + self.min_stop_step):   # nur nach oben, nicht bei jedem Cent
            self.c.read("AmendOrder", txid=cur["txid"], trigger_price=self._p(stop_price))
            cur["price"] = float(self._p(stop_price))

    def sync(self, symbol: str) -> Fill | None:
        return self._settle_stop(symbol, cancel=False)

    def sell(self, symbol: str, qty: float, price: float) -> Fill:
        stopped = self._settle_stop(symbol, cancel=True)
        left = self.holdings.get(symbol, 0.0)
        if left < self.min_qty:
            if stopped:
                return stopped          # der Stop an der Börse war schneller
            raise BrokerError("Keine Position zum Verkaufen")
        on_account = float(self.c.read("Balance").get(self.base, 0))
        if on_account < left * 0.999:
            raise BrokerUncertain(f"Auf dem Konto liegen {on_account} statt {left} {self.base}. "
                                  "Bitte Konto prüfen, dann Bot neu starten.")
        txid = self._order("Verkauf", type="sell", ordertype="market", volume=self._q(left))
        fill = self._fill(self._wait_done(txid))
        if not fill:
            raise BrokerError("Verkauf nicht ausgeführt")
        self._book_sell(symbol, fill)
        if stopped:
            qty_all = fill.qty + stopped.qty
            fill = Fill(qty_all, (fill.qty * fill.price + stopped.qty * stopped.price) / qty_all,
                        fill.fee + stopped.fee)
        return fill
