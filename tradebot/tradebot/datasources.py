"""Echte Datenquellen (oeffentlich, ohne Konto): Kurs, Fear&Greed, Nachrichten-RSS.

Alle Parser sind reine Funktionen und per Test abgesichert. Die Live-Abrufe selbst
sind in der Entwicklungsumgebung NICHT getestet worden (Netzwerk gesperrt)."""
from __future__ import annotations

import json
import urllib.request

KRAKEN_TICKER = "https://api.kraken.com/0/public/Ticker?pair=XBTEUR"
FEAR_GREED = "https://api.alternative.me/fng/"
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]


class DataError(RuntimeError):
    pass


def _get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tradebot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read()


def parse_kraken_price(raw: bytes) -> float:
    d = json.loads(raw)
    if d.get("error"):
        raise DataError(f"Kraken: {d['error']}")
    result = d.get("result") or {}
    if not result:
        raise DataError("Kraken: leere Antwort")
    price = float(next(iter(result.values()))["c"][0])
    if price <= 0:
        raise DataError("Kraken: ungueltiger Preis")
    return price


def parse_fear_greed(raw: bytes) -> int:
    v = int(json.loads(raw)["data"][0]["value"])
    if not 0 <= v <= 100:
        raise DataError("Fear&Greed ausserhalb 0-100")
    return v


def get_price() -> float:
    return parse_kraken_price(_get(KRAKEN_TICKER))


def get_fear_greed() -> int:
    return parse_fear_greed(_get(FEAR_GREED))
