"""Prüft den Kraken-Zugang, OHNE zu handeln.

    export KRAKEN_API_KEY=...      (nur auf diesem Gerät eintippen, nie in einen Chat)
    export KRAKEN_API_SECRET=...
    python -m tradebot.kraken_check

Es werden nur Daten gelesen und zwei Orders im Prüfmodus (`validate`) an Kraken geschickt. Zur
Sicherheit sind diese Orders so gewählt, dass sie selbst als echte Orders nichts auslösen könnten:
Mindestmenge, Kauflimit und Stop beim halben Marktpreis."""
from __future__ import annotations

import os
import sys

from .broker import BrokerError
from .kraken import KrakenClient, TransportError


def check(client: KrakenClient, pair: str = "XBTEUR", out=print) -> bool:
    ok = True

    def step(title, fn):
        nonlocal ok
        try:
            out(f"✓ {title}: {fn()}")
        except (BrokerError, TransportError, KeyError, ValueError, StopIteration) as e:
            ok = False
            out(f"✗ {title}: {e}")

    info: dict = {}
    price = [0.0]

    def pair_info():
        info.update(next(iter(client.public("AssetPairs", pair=pair).values())))
        return f"{info['altname']} {info['status']}, Mindestmenge {info['ordermin']}"

    def ticker():
        price[0] = float(next(iter(client.public("Ticker", pair=pair).values()))["c"][0])
        return f"{price[0]:.1f} EUR"

    def balance():
        b = client.read("Balance")
        return f"{float(b.get(info.get('quote', 'ZEUR'), 0)):.2f} EUR, {float(b.get(info.get('base', 'XXBT'), 0)):.8f} BTC"

    def fee():
        f = next(iter(client.read("TradeVolume", pair=pair)["fees"].values()))["fee"]
        return f"{float(f):.2f} % je Kauf/Verkauf"

    def validate(side, ordertype, **extra):
        r = client.private("AddOrder", pair=pair, type=side, ordertype=ordertype, oflags="fciq",
                           volume=info["ordermin"], price=f"{price[0] / 2:.{int(info['pair_decimals'])}f}",
                           validate="true", **extra)
        if r.get("txid"):   # darf im Prüfmodus nicht passieren -> sofort löschen und laut melden
            for txid in r["txid"]:
                client.read("CancelOrder", txid=txid)
            raise BrokerError(f"Kraken hat trotz Prüfmodus eine Order angelegt ({r['txid']}); sie wurde gelöscht")
        return r.get("descr", {}).get("order", "ok")

    step("Kraken erreichbar", lambda: client.public("SystemStatus")["status"])
    step("Handelspaar", pair_info)
    step("Kurs", ticker)
    if not ok:
        return False
    step("Konto lesen (Recht: Query Funds)", balance)
    step("Gebühr", fee)
    step("Offene Orders lesen (Recht: Query Open Orders)", lambda: f"{len(client.read('OpenOrders').get('open', {}))} offen")
    step("Kauf-Order prüfen (Recht: Create & Modify Orders)", lambda: validate("buy", "limit", timeinforce="IOC"))
    try:
        out(f"✓ Stop-Order prüfen: {validate('sell', 'stop-loss')}")
    except BrokerError as e:
        if "Insufficient" in str(e):
            out("– Stop-Order prüfen: nicht möglich, weil noch kein BTC auf dem Konto liegt (das ist normal)")
        else:
            ok = False
            out(f"✗ Stop-Order prüfen: {e}")
    except TransportError as e:
        ok = False
        out(f"✗ Stop-Order prüfen: {e}")
    return ok


def main() -> int:
    key, secret = os.environ.get("KRAKEN_API_KEY", ""), os.environ.get("KRAKEN_API_SECRET", "")
    if not key or not secret:
        print("KRAKEN_API_KEY und KRAKEN_API_SECRET sind nicht gesetzt. Siehe Kopf dieser Datei.")
        return 2
    ok = check(KrakenClient(key, secret))
    print("\nAlles in Ordnung. Es wurde nichts gehandelt." if ok else "\nEs gibt Probleme, siehe oben. Es wurde nichts gehandelt.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
