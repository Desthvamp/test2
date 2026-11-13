"""Einfache Nachrichten-Stimmung per Schluesselwoerter (keine KI, kein Heilsversprechen).

Liefert einen Wert zwischen -1 (sehr negativ) und +1 (sehr positiv)."""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET

POSITIVE = {"surge", "rally", "approval", "adoption", "etf inflow", "upgrade", "record", "bullish", "partnership", "gains"}
NEGATIVE = {"hack", "exploit", "ban", "lawsuit", "crash", "outflow", "bearish", "liquidation", "fraud", "sell-off", "selloff", "delist"}


def score_text(text: str) -> float:
    t = text.lower()
    pos = sum(1 for w in POSITIVE if re.search(r"\b" + re.escape(w) + r"\b", t))
    neg = sum(1 for w in NEGATIVE if re.search(r"\b" + re.escape(w) + r"\b", t))
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def fetch_headlines(feed_url: str, timeout: float = 10.0) -> list[str]:
    req = urllib.request.Request(feed_url, headers={"User-Agent": "tradebot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL kommt aus Config
        root = ET.fromstring(resp.read())
    return [item.findtext("title", default="") for item in root.iter("item")]


def aggregate(headlines: list[str]) -> float:
    scores = [score_text(h) for h in headlines if h]
    return sum(scores) / len(scores) if scores else 0.0
