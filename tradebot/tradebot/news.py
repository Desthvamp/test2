"""Einfache Nachrichten-Stimmung per Schlüsselwörter (keine KI, kein Heilsversprechen).

Jede Schlagzeile wird als positiv, negativ oder neutral eingestuft. Die Stimmung ist
(positiv - negativ) / (positiv + negativ), also zwischen -1 und +1. Neutrale Schlagzeilen
zählen nicht mit; bei weniger als MIN_SIGNAL eindeutigen Schlagzeilen gilt die Stimmung als 0."""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_POS = [r"surg\w*", r"soar\w*", r"rall(?:y|ies|ied|ying)", r"rebound\w*", r"recover\w*", r"jump\w*",
        r"climb\w*", r"gain\w*", r"rises?", r"rose", r"rising", r"breakout\w*", r"bullish", r"approv\w*",
        r"adopt\w*", r"inflows?", r"upgrad\w*", r"partner\w*", r"accumulat\w*", r"outperform\w*",
        r"all-time high", r"record high", r"new high", r"buys"]
_NEG = [r"hack\w*", r"exploit\w*", r"ban(?:s|ned)?", r"lawsuit\w*", r"sue[sd]?", r"crash\w*", r"plung\w*",
        r"plummet\w*", r"slump\w*", r"tumbl\w*", r"sell-?off\w*", r"outflows?", r"bearish", r"liquidat\w*",
        r"fraud\w*", r"scam\w*", r"delist\w*", r"bankrupt\w*", r"insolven\w*", r"collaps\w*", r"panic\w*",
        r"crackdown\w*", r"dump\w*", r"drop(?:s|ped)?", r"falls?", r"fell", r"falling", r"declin\w*",
        r"loss(?:es)?", r"warn\w*", r"fears?"]
POS_RE = re.compile(r"\b(?:" + "|".join(_POS) + r")\b")
NEG_RE = re.compile(r"\b(?:" + "|".join(_NEG) + r")\b")
MIN_SIGNAL = 3


def score_text(text: str) -> float:
    t = text.lower()
    pos, neg = len(POS_RE.findall(t)), len(NEG_RE.findall(t))
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


@dataclass
class NewsSummary:
    score: float
    pos: int = 0
    neg: int = 0
    total: int = 0

    def info(self) -> str:
        return f"{self.pos} positiv / {self.neg} negativ von {self.total} Schlagzeilen"


def summarize(headlines: list[str]) -> NewsSummary:
    scores = [score_text(h) for h in dict.fromkeys(h for h in headlines if h)]
    pos = sum(1 for s in scores if s > 0)
    neg = sum(1 for s in scores if s < 0)
    score = (pos - neg) / (pos + neg) if pos + neg >= MIN_SIGNAL else 0.0
    return NewsSummary(score, pos, neg, len(scores))


def aggregate(headlines: list[str]) -> float:
    return summarize(headlines).score


def fetch_headlines(feed_url: str, timeout: float = 10.0) -> list[str]:
    req = urllib.request.Request(feed_url, headers={"User-Agent": "tradebot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL kommt aus Config
        root = ET.fromstring(resp.read())
    return [item.findtext("title", default="") for item in root.iter("item")]
