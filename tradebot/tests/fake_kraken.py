"""Nachgebaute Kraken-Börse für Tests, nach der offiziellen Doku (Felder, Statuswerte, Fehlerformat)."""
import base64
import json
import time
from urllib.parse import parse_qsl, urlparse

from tradebot.kraken import KrakenClient, TransportError, sign

SECRET = base64.b64encode(b"test-secret-not-a-real-key").decode()
FEE = 0.008


class Err(Exception):
    pass


class FakeKraken:
    def __init__(self, eur=500.0, btc=0.0, price=100.0):
        self.eur, self.btc, self.ask, self.bid = eur, btc, price, price
        self.orders: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: set[str] = set()       # Methoden, deren Antwort "verloren geht"
        self.last_nonce = 0
        self.n = 0
        self.clients = 0

    def client(self) -> KrakenClient:
        # Jeder neue Client steht für einen späteren Programmstart: seine Uhr ist weiter.
        self.clients += 1
        ahead = self.clients * 10.0
        return KrakenClient("key", SECRET, http=self.http, clock=lambda: time.time() + ahead)

    def set_price(self, price: float) -> None:
        self.ask = self.bid = price

    # --- Transport ---
    def http(self, url, body, headers):
        u = urlparse(url)
        method = u.path.rsplit("/", 1)[1]
        try:
            if "/public/" in u.path:
                result = getattr(self, "p_" + method)(dict(parse_qsl(u.query)))
            else:
                data = dict(parse_qsl(body.decode()))
                assert headers["API-Key"] == "key"
                assert headers["API-Sign"] == sign(u.path, data, SECRET), "Signatur passt nicht zu den gesendeten Daten"
                if int(data["nonce"]) <= self.last_nonce:
                    raise Err("EAPI:Invalid nonce")
                self.last_nonce = int(data["nonce"])
                self.calls.append((method, data))
                result = getattr(self, "m_" + method)(data)   # die Börse führt aus ...
                if method in self.fail:
                    raise TransportError("timeout")          # ... aber die Antwort kommt nicht an
        except Err as e:
            return json.dumps({"error": [str(e)]}).encode()
        return json.dumps({"error": [], "result": result}).encode()

    # --- öffentlich ---
    def p_AssetPairs(self, q):
        return {"XXBTZEUR": {"altname": "XBTEUR", "base": "XXBT", "quote": "ZEUR", "pair_decimals": 1,
                             "lot_decimals": 8, "ordermin": "0.00006", "costmin": "0.45", "status": "online"}}

    def p_SystemStatus(self, q):
        return {"status": "online", "timestamp": "2026-11-13T21:00:00Z"}

    def p_Ticker(self, q):
        return {"XXBTZEUR": {"c": [f"{self.ask:.5f}", "0.001"]}}

    # --- privat ---
    def m_OpenOrders(self, d):
        return {"open": {k: v for k, v in self.orders.items() if v["status"] == "open"}}

    def m_Balance(self, d):
        return {"ZEUR": f"{self.eur:.4f}", "XXBT": f"{self.btc:.10f}"}

    def m_TradeVolume(self, d):
        return {"currency": "ZUSD", "fees": {"XXBTZEUR": {"fee": "0.8000"}}}

    def _free_btc(self):
        return self.btc - sum(o["_vol"] for o in self.orders.values() if o["status"] == "open")

    def _execute(self, o, price):
        vol = o["_vol"]
        cost = vol * price
        fee = cost * FEE
        if o["descr"]["type"] == "buy":
            self.eur -= cost + fee
            self.btc += vol
        else:
            self.eur += cost - fee
            self.btc -= vol
        o.update(status="closed", vol_exec=f"{vol:.8f}", cost=f"{cost:.5f}", fee=f"{fee:.5f}", price=f"{price:.1f}")

    def m_AddOrder(self, d):
        assert d["pair"] == "XBTEUR" and d["oflags"] == "fciq"
        vol = float(d["volume"])
        if vol < 0.00006:
            raise Err("EGeneral:Invalid arguments:volume minimum not met")
        if d.get("validate") == "true":
            if d["type"] == "sell" and self._free_btc() < vol - 1e-12:
                raise Err("EOrder:Insufficient funds")
            return {"descr": {"order": f"{d['type']} {d['volume']} XBTEUR @ {d['ordertype']} {d.get('price', '')}"}}
        self.n += 1
        txid = f"OTEST-{self.n}"
        o = {"status": "open", "vol": d["volume"], "vol_exec": "0.00000000", "cost": "0.00000", "fee": "0.00000",
             "price": "0.00000", "_vol": vol,
             "descr": {"pair": "XBTEUR", "type": d["type"], "ordertype": d["ordertype"], "price": d.get("price", "0")}}
        kind = (d["type"], d["ordertype"])
        if kind == ("buy", "limit"):
            assert d["timeinforce"] == "IOC"
            if self.ask <= float(d["price"]) and vol * self.ask * (1 + FEE) <= self.eur:
                self._execute(o, self.ask)
            else:
                o["status"] = "canceled"            # IOC: nicht sofort ausführbar -> verfällt
        elif kind == ("sell", "market"):
            if self._free_btc() < vol - 1e-12:
                raise Err("EOrder:Insufficient funds")
            self._execute(o, self.bid)
        elif kind == ("sell", "stop-loss"):
            if self._free_btc() < vol - 1e-12:
                raise Err("EOrder:Insufficient funds")
        else:
            raise Err("EGeneral:Invalid arguments:ordertype")
        self.orders[txid] = o
        return {"descr": {"order": "..."}, "txid": [txid]}

    def m_QueryOrders(self, d):
        o = self.orders.get(d["txid"])
        return {d["txid"]: {k: v for k, v in o.items() if not k.startswith("_")}} if o else {}

    def m_CancelOrder(self, d):
        o = self.orders.get(d["txid"])
        if not o or o["status"] != "open":
            raise Err("EOrder:Unknown order")
        o["status"] = "canceled"
        return {"count": 1}

    def m_AmendOrder(self, d):
        o = self.orders.get(d["txid"])
        if not o or o["status"] != "open":
            raise Err("EOrder:Unknown order")
        o["descr"]["price"] = d["trigger_price"]
        return {"amend_id": "ATEST-1"}

    # --- Testhilfen ---
    def open_stops(self):
        return [o for o in self.orders.values() if o["status"] == "open" and o["descr"]["ordertype"] == "stop-loss"]

    def trigger_stop(self, price=None):
        """Der Kurs fällt unter den Stop: Kraken führt die Stop-Order aus (auch wenn der Bot aus ist)."""
        (o,) = self.open_stops()
        self._execute(o, float(o["descr"]["price"]) if price is None else price)

    def sent(self, method):
        return [d for m, d in self.calls if m == method]
