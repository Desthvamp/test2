"""Kaufsignal. Bewusst einfach und nachvollziehbar; jede Bedingung wird im Dashboard angezeigt.

Gekauft wird nur, wenn ALLE Bedingungen erfüllt sind:
1. genug Kursdaten gesammelt
2. Trend: Kurs liegt über seinem Durchschnitt der letzten Stunde
3. Momentum: Kurs ist höher als vor 15 Minuten
4. Fear&Greed: Markt ist nicht überhitzt
5. Nachrichten: Stimmung über der Schwelle. Bei extremer Angst (Fear&Greed niedrig) zählen die
   Nachrichten NICHT: dann kauft der Bot, sobald der Kurs dreht. Das ist die bewusste Wette
   "in der Angst kaufen". Sie kauft auch kurze Erholungen in einem fallenden Markt.
6. keine Pause nach einem Verkauf (verhindert ständiges Rein-Raus, das nur Gebühren kostet)

Die Schwellen sind Faustwerte und NICHT durch Tests an historischen Daten belegt."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyConfig:
    trend_window_s: float = 3600        # Fenster für den Durchschnitt
    min_history_s: float = 1800         # so lange muss der Bot Kurse gesammelt haben
    min_samples: int = 20
    entry_trend_pct: float = 0.003      # Kurs mind. 0,3 % über dem Durchschnitt
    momentum_lookback_s: float = 900
    news_threshold: float = 0.2
    fear_level: int = 25                # Fear&Greed <= 25 = extreme Angst
    fear_ignores_news: bool = True      # bei extremer Angst zählen die Nachrichten nicht
    greed_level: int = 75               # Fear&Greed >= 75 = überhitzt, kein Kauf
    cooldown_s: float = 6 * 3600        # Pause nach einem Verkauf


@dataclass
class Check:
    ok: bool
    text: str


@dataclass
class Signal:
    checks: list[Check]

    @property
    def buy(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def entry_signal(history: list[tuple[float, float]], price: float, news: float | None,
                 fear_greed: int | None, now: float, last_exit_ts: float,
                 cfg: StrategyConfig) -> Signal:
    """history: (Zeit, Kurs)-Paare, ältestes zuerst, inklusive des aktuellen Kurses."""
    window = [(t, p) for t, p in history if t >= now - cfg.trend_window_s]
    span = now - window[0][0] if window else 0.0
    enough = span >= cfg.min_history_s and len(window) >= cfg.min_samples
    checks = [Check(enough, f"Kursdaten: {min(span, cfg.min_history_s) / 60:.0f} von "
                            f"{cfg.min_history_s / 60:.0f} Min gesammelt")]

    if enough:
        avg = sum(p for _, p in window) / len(window)
        diff = price / avg - 1
        checks.append(Check(diff >= cfg.entry_trend_pct,
                            f"Trend: Kurs {diff:+.2%} zum Stunden-Schnitt (nötig {cfg.entry_trend_pct:+.2%})"))
        past = [p for t, p in window if t <= now - cfg.momentum_lookback_s]
        mom = price / past[-1] - 1 if past else 0.0
        checks.append(Check(bool(past) and mom > 0,
                            f"Momentum: {mom:+.2%} gegenüber vor {cfg.momentum_lookback_s / 60:.0f} Min (nötig > 0)"))
    else:
        checks.append(Check(False, "Trend: noch zu wenig Daten"))
        checks.append(Check(False, "Momentum: noch zu wenig Daten"))

    fear = fear_greed is not None and fear_greed <= cfg.fear_level
    skip_news = fear and cfg.fear_ignores_news
    if fear_greed is None:
        checks.append(Check(True, "Fear&Greed: nicht verfügbar, normale News-Schwelle"))
    elif fear_greed >= cfg.greed_level:
        checks.append(Check(False, f"Fear&Greed {fear_greed}: Markt überhitzt, kein Kauf"))
    elif fear:
        checks.append(Check(True, f"Fear&Greed {fear_greed}: extreme Angst"))
    else:
        checks.append(Check(True, f"Fear&Greed {fear_greed}: normal"))

    shown = "nicht verfügbar" if news is None else f"{news:+.2f}"
    if skip_news:
        checks.append(Check(True, f"Nachrichten: {shown}, zählen bei extremer Angst nicht"))
    elif news is None:
        checks.append(Check(False, "Nachrichten: nicht verfügbar, kein Kauf"))
    else:
        checks.append(Check(news >= cfg.news_threshold,
                            f"Nachrichten: {shown} (nötig mind. {cfg.news_threshold:+.2f})"))

    wait = cfg.cooldown_s - (now - last_exit_ts) if last_exit_ts else 0.0
    if wait > 0:
        checks.append(Check(False, f"Pause nach Verkauf: noch {wait / 3600:.1f} Std"))
    else:
        checks.append(Check(True, "Pause nach Verkauf: keine"))
    return Signal(checks)
