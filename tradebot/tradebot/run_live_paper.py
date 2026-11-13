"""Paper-Trading mit echten Kursen und echten Nachrichten. KEIN echtes Geld.

Bei jedem Datenfehler wird der Tick uebersprungen (kein Trade auf Verdacht).
Dashboard: http://<IP-des-Rechners>:8080/dashboard.html"""
from __future__ import annotations

import functools
import http.server
import json
import threading
import time
from pathlib import Path

from .broker import PaperBroker
from .datasources import RSS_FEEDS, DataError, get_fear_greed, get_price
from .engine import Engine, EngineConfig
from .news import aggregate, fetch_headlines
from .risk import RiskConfig

PRICE_EVERY = 30      # Sekunden zwischen Kursabfragen
NEWS_EVERY = 600      # Sekunden zwischen Nachrichten-Abfragen
MAX_NEWS_AGE = 3600   # aeltere Stimmung gilt als ungueltig -> neutral


def serve(directory: Path, port: int = 8080) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def refresh_news() -> float:
    headlines: list[str] = []
    for url in RSS_FEEDS:
        try:
            headlines += fetch_headlines(url)[:20]
        except Exception:  # eine Quelle darf ausfallen
            continue
    if not headlines:
        raise DataError("keine Nachrichten abrufbar")
    return aggregate(headlines)


def main() -> None:
    here = Path(__file__).resolve().parent.parent
    cfg = EngineConfig(state_file=str(here / "state.json"), kill_file=str(here / "KILL"))
    engine = Engine(broker=PaperBroker(cash=100.0, fee_pct=RiskConfig().fee_pct),
                    risk_cfg=RiskConfig(), cfg=cfg)
    serve(here)
    news, news_ts, last_price, fg = 0.0, 0.0, 0.0, None
    while True:
        now = time.time()
        try:
            if now - news_ts >= NEWS_EVERY:
                news, news_ts = refresh_news(), now
                try:
                    fg = get_fear_greed()
                except Exception:
                    fg = None
            if now - news_ts > MAX_NEWS_AGE:
                news = 0.0
            last_price = get_price()
            engine.tick(last_price, news)
            engine._say(f"Kurs {last_price:.2f} | News {news:+.2f} | Fear&Greed {fg}")
        except Exception as e:  # Datenfehler -> nichts tun, nur loggen
            engine._say(f"Tick uebersprungen: {e}")
        engine.save(last_price)  # auch ohne Kurs speichern, damit Fehler im Dashboard sichtbar sind
        time.sleep(PRICE_EVERY)


if __name__ == "__main__":
    main()
