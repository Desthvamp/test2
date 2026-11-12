"""Startet den Paper-Bot mit simulierten Kursen (Random-Walk). Nur zum Testen der Logik.

Echte Kursdaten und echte Nachrichten sind noch nicht angebunden."""
from __future__ import annotations

import random
import time

from .broker import PaperBroker
from .engine import Engine, EngineConfig
from .risk import RiskConfig


def main() -> None:
    engine = Engine(broker=PaperBroker(cash=100.0, fee_pct=RiskConfig().fee_pct),
                    risk_cfg=RiskConfig(), cfg=EngineConfig())
    price = 60_000.0
    while True:
        price *= 1 + random.gauss(0, 0.004)
        news = random.uniform(-1, 1)  # Platzhalter fuer echte Nachrichten-Stimmung
        engine.tick(price, news)
        engine.save(price)
        time.sleep(1)


if __name__ == "__main__":
    main()
