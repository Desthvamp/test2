"""Paper-Trading mit echten Kursen und echten Nachrichten. KEIN echtes Geld.

- Kein Kurs abrufbar  -> Tick wird übersprungen (kein Trade auf Verdacht).
- Keine Nachrichten   -> es wird nichts gekauft, aber offene
  Positionen (Stop-Loss, Trailing-Stop, Take-Profit) werden weiter überwacht.
- Der Stand wird in state.json gespeichert und beim nächsten Start wieder geladen.

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
from .news import NewsSummary, fetch_headlines, summarize
from .risk import RiskConfig

PRICE_EVERY = 30      # Sekunden zwischen Kursabfragen
NEWS_EVERY = 600      # Sekunden zwischen Nachrichten-Abfragen
NEWS_RETRY = 60       # nach einem Fehler früher erneut versuchen
MAX_NEWS_AGE = 3600   # ältere Stimmung gilt als ungültig -> neutral
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


def refresh_news() -> NewsSummary:
    headlines: list[str] = []
    for url in RSS_FEEDS:
        try:
            headlines += fetch_headlines(url)[:20]
        except Exception:  # eine Quelle darf ausfallen
            continue
    if not headlines:
        raise DataError("keine Quelle erreichbar")
    return summarize(headlines)


@dataclass
class Runner:
    engine: Engine
    price_fn: Callable[[], float] = get_price
    news_fn: Callable[[], NewsSummary] = refresh_news
    fg_fn: Callable[[], int] = get_fear_greed
    news: NewsSummary | None = None
    news_ok_ts: float = 0.0           # letzter erfolgreicher Nachrichten-Abruf
    next_news_ts: float = 0.0
    fg: int | None = None
    last_price: float = 0.0

    def step(self, now: float) -> None:
        e = self.engine
        if now >= self.next_news_ts:
            try:
                self.news, self.news_ok_ts = self.news_fn(), now
                self.next_news_ts = now + NEWS_EVERY
            except Exception as err:
                self.next_news_ts = now + NEWS_RETRY
                e._say(f"Nachrichten nicht abrufbar ({err}), solange wird nichts gekauft")
            try:
                self.fg = self.fg_fn()
            except Exception:
                self.fg = None
        fresh = self.news is not None and now - self.news_ok_ts <= MAX_NEWS_AGE
        news = self.news.score if fresh else None

        try:
            price = self.price_fn()
        except Exception as err:
            e._say(f"Kein Kurs abrufbar ({err}), Tick übersprungen")
        else:
            self.last_price = price
            e.start_day(time.strftime("%Y-%m-%d", time.localtime(now)),
                        e.broker.equity({e.cfg.symbol: price}))
            try:
                e.tick(price, news, now, self.fg)
            except Exception as err:   # ein unerwarteter Fehler darf den Bot nicht beenden
                e._say(f"Interner Fehler ({type(err).__name__}: {err}), weiter beim nächsten Kurs")
            e.status = {"price": price, "price_ts": now, "news": news,
                        "news_info": self.news.info() if fresh else "", "fear_greed": self.fg}
            ok = sum(c.ok for c in e.signal)
            sig = "Position offen" if e.positions else f"Kaufsignal {ok}/{len(e.signal)}"
            e._say(f"Kurs {price:.2f} | News {'keine' if news is None else f'{news:+.2f}'} | "
                   f"Fear&Greed {self.fg} | {sig}", log=False)
        e.save(self.last_price)


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
        print(f"FEHLER: Port {PORT} ist belegt ({e}). Läuft der Bot schon in einem anderen Fenster?", flush=True)
        return
    print("=" * 50, flush=True)
    print("Bot läuft (PAPER-MODUS, kein echtes Geld).", flush=True)
    print(f"Dashboard: http://localhost:{PORT}/dashboard.html", flush=True)
    print("Stoppen: CTRL und dann c", flush=True)
    print("=" * 50, flush=True)
    runner = Runner(engine, last_price=engine.history[-1][1] if engine.history else 0.0)
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
