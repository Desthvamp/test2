# Tradebot (Paper-Modus)

Autonomer Trading-Bot für BTC/EUR. **Läuft nur mit Spielgeld.** Einen Starter für echtes Geld gibt es nicht.

## Starten

    cd tradebot
    python -m tradebot.run_live_paper

Dashboard im Browser: `http://localhost:8080/dashboard.html`. Stoppen: CTRL+C.
Der Stand liegt in `state.json` und wird nach einem Neustart weitergeführt. Zurücksetzen: Datei löschen.

## Wie der Bot entscheidet

Gekauft wird nur, wenn alle Bedingungen gleichzeitig erfüllt sind (`strategy.py`, im Dashboard sichtbar):

1. mindestens 30 Minuten Kursdaten gesammelt
2. Trend: Kurs mindestens 0,3 % über dem Durchschnitt der letzten Stunde
3. Momentum: Kurs höher als vor 15 Minuten
4. Fear & Greed unter 75 (Markt nicht überhitzt)
5. Nachrichten-Stimmung mindestens +0,2; bei extremer Angst (Fear & Greed bis 25) reicht „nicht negativ"
6. keine Pause: nach jedem Verkauf wartet der Bot 6 Stunden

Verkauft wird bei Stop-Loss (−5 %), Gewinnmitnahme (+10 %), nachgezogenem Stop (ab +6 % Gewinn folgt der Stop
dem Kurs mit 3 % Abstand) oder Not-Aus (Datei `KILL` im Ordner `tradebot` anlegen).

Risiko (`risk.py`): pro Trade höchstens 2 % des Kapitals Verlust inklusive Gebühren, ab −5 % Tagesverlust
keine neuen Käufe. Gebühr im Paper-Modus: 1,5 % je Kauf und Verkauf (Annahme, nicht verifiziert).

**Alle Schwellen sind Faustwerte und nicht an historischen Daten geprüft.** Die Nachrichten-Stimmung ist eine
einfache Schlüsselwort-Zählung in englischen Schlagzeilen, keine KI.

## Datenquellen

Kurs: Kraken (öffentlich). Stimmung: alternative.me Fear & Greed. Nachrichten: RSS von Cointelegraph und CoinDesk.
Kein Kurs: Tick wird übersprungen. Keine Nachrichten: es wird nichts gekauft, offene Positionen werden weiter überwacht.

## Offen

- Bitpanda (`bitpanda.py`): nach offizieller Doku gebaut, nur gegen simulierte Antworten getestet. Bitpanda kennt
  keine Stop-Orders; der Stop-Loss läuft also nur, solange der Bot läuft.
- Dauerbetrieb: Der Bot braucht ein Gerät, das rund um die Uhr läuft.

## Tests

    cd tradebot && PYTHONPATH=. python -m pytest -q tests
