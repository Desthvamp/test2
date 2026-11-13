"""Paper-Trading mit echten Kursen und echten Nachrichten. KEIN echtes Geld.

- Kein Kurs abrufbar  -> Tick wird uebersprungen (kein Trade auf Verdacht).
- Keine Nachrichten   -> Stimmung gilt als neutral: es wird nichts gekauft, aber offene
  Positionen (Stop-Loss, Trailing-Stop, Take-Profit) werden weiter ueberwacht.
- Der Stand wird in state.json gespeichert und beim naechsten Start wieder geladen.

Dashboard: http://localhost:8080/dashboard.html"""
from __future__ import annotations

import http.server
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .broker import PaperBroker
from .datasources import RSS_FEEDS, DataError, get_fear_greed, get_price
from .engine import Engine, EngineConfig
from .news import aggregate, fetch_headlines
from .risk import RiskConfig

PRICE_EVERY = 30      # Sekunden zwischen Kursabfragen
NEWS_EVERY = 600      # Sekunden zwischen Nachrichten-Abfragen
NEWS_RETRY = 60       # nach einem Fehler frueher erneut versuchen
MAX_NEWS_AGE = 3600   # aeltere Stimmung gilt als ungueltig -> neutral
PORT = 8080
SERVED = {"/dashboard.html", "/state.json"}   # nichts anderes wird ausgeliefert


def serve(directory: Path, port: int = PORT) -> None:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/dashboard.html")
                self.end_headers()
            elif path in SERVED:
                super().do_GET()
            else:
                self.send_error(404)

        def log_message(self, *a):  # Terminal ruhig halten
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def refresh_news() -> float:
    headlines: list[str] = []
    for url in RSS_FEEDS:
        try:
            headlines += fetch_headlines(url)[:20]
        except Exception:  # eine Quelle darf ausfallen
            continue
    if not headlines:
        raise DataError("keine Quelle erreichbar")
    return aggregate(headlines)


@dataclass
class Runner:
    engine: Engine
    price_fn: Callable[[], float] = get_price
    news_fn: Callable[[], float] = refresh_news
    fg_fn: Callable[[], int] = get_fear_greed
    news: float = 0.0
    news_ok_ts: float | None = None   # letzter erfolgreicher Nachrichten-Abruf
    next_news_ts: float = 0.0
    fg: int | None = None
    last_price: float = 0.0

    def step(self, now: float) -> None:
        if now >= self.next_news_ts:
            try:
                self.news, self.news_ok_ts = self.news_fn(), now
                self.next_news_ts = now + NEWS_EVERY
            except Exception as e:
                self.next_news_ts = now + NEWS_RETRY
                self.engine._say(f"Nachrichten nicht abrufbar ({e}) - es wird nichts gekauft")
            try:
                self.fg = self.fg_fn()
            except Exception:
                self.fg = None
        fresh = self.news_ok_ts is not None and now - self.news_ok_ts <= MAX_NEWS_AGE
        news = self.news if fresh else 0.0

        try:
            price = self.price_fn()
        except Exception as e:
            self.engine._say(f"Tick uebersprungen, kein Kurs: {e}")
        else:
            self.last_price = price
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            self.engine.start_day(day, self.engine.broker.equity({self.engine.cfg.symbol: price}))
            self.engine.tick(price, news)
            self.engine._say(f"Kurs {price:.2f} | News {news:+.2f}{'' if fresh else ' (keine)'} | Fear&Greed {self.fg}")
        self.engine.save(self.last_price)


def main() -> None:
    here = Path(__file__).resolve().parent.parent
    cfg = EngineConfig(state_file=str(here / "state.json"), kill_file=str(here / "KILL"))
    engine = Engine(broker=PaperBroker(cash=100.0, fee_pct=RiskConfig().fee_pct),
                    risk_cfg=RiskConfig(), cfg=cfg,
                    on_log=lambda line: print(line, flush=True))
    resumed = engine.restore()
    try:
        serve(here)
    except OSError as e:
        print(f"FEHLER: Port {PORT} ist belegt ({e}). Laeuft der Bot schon in einem anderen Fenster?", flush=True)
        return
    print("=" * 50, flush=True)
    print("Bot laeuft (PAPER-MODUS, kein echtes Geld).", flush=True)
    print(f"Dashboard: http://localhost:{PORT}/dashboard.html", flush=True)
    print("Stoppen: CTRL und dann c", flush=True)
    print("=" * 50, flush=True)
    runner = Runner(engine, last_price=engine.prices[-1] if engine.prices else 0.0)
    engine._say(f"Start: {'alter Stand geladen' if resumed else 'neuer Stand'}, Cash {engine.broker.cash:.2f}")
    try:
        while True:
            runner.step(time.time())
            time.sleep(PRICE_EVERY)
    except KeyboardInterrupt:
        engine.save(runner.last_price)
        print("\nBot gestoppt. Stand gespeichert.", flush=True)


if __name__ == "__main__":
    main()
