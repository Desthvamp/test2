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
5. Nachrichten-Stimmung mindestens +0,2; bei extremer Angst (Fear & Greed bis 25) zählen die Nachrichten nicht,
   dann kauft der Bot, sobald der Kurs dreht (bewusste Wette, kauft auch Erholungen im fallenden Markt)
6. keine Pause: nach jedem Verkauf wartet der Bot 6 Stunden

Verkauft wird bei Stop-Loss (−5 %), Gewinnmitnahme (+10 %), nachgezogenem Stop (ab +6 % Gewinn folgt der Stop
dem Kurs mit 3 % Abstand) oder Not-Aus (Datei `KILL` im Ordner `tradebot` anlegen).

Nachkauf: Liegt der Kurs mindestens 3 % unter dem Einstieg und steigt wieder (höher als vor 15 Minuten), kauft
der Bot einmal für denselben Betrag nach. Stop, Ziel und Trailing gelten danach ab dem Durchschnittskurs.

Risiko (`risk.py`): pro Position höchstens 2 % des Kapitals Verlust inklusive Gebühren, nach einem Nachkauf
rund 4 %. Ab −5 % Tagesverlust keine neuen Käufe und keine Nachkäufe. Gebühr im Paper-Modus: 0,8 % je Kauf und Verkauf (Kraken, niedrigster Tarif, Stand November 2026).

**Alle Schwellen sind Faustwerte und nicht an historischen Daten geprüft.** Die Nachrichten-Stimmung ist eine
einfache Schlüsselwort-Zählung in englischen Schlagzeilen, keine KI.

## Datenquellen

Kurs: Kraken (öffentlich). Stimmung: alternative.me Fear & Greed. Nachrichten: RSS von Cointelegraph und CoinDesk.
Kein Kurs: Tick wird übersprungen. Keine Nachrichten: es wird nichts gekauft, offene Positionen werden weiter überwacht.

## Kraken (für später, echtes Geld)

`kraken.py` ist nach der offiziellen Kraken-Doku gebaut:

- Kauf als Limit-Order, die sofort ausgeführt wird oder verfällt (höchstens 0,5 % über dem aktuellen Kurs).
- Der Stop-Loss liegt als echte Order bei Kraken und wird dort nachgezogen. Er greift auch, wenn der Bot
  oder das Gerät ausfällt.
- Gewinnmitnahme und Not-Aus macht der Bot selbst (Stop-Order löschen, Markt-Verkauf).
- Der Bot verwaltet nur das zugewiesene Kapital, nicht das ganze Konto.
- Ist nach einem Netzwerkfehler unklar, ob eine Order ankam, kauft der Bot nichts mehr, bis jemand
  nachgesehen und ihn neu gestartet hat.

Geprüft: Signatur gegen das Beispiel aus der Doku, öffentliche Abfragen gegen die echte Schnittstelle,
alles Übrige gegen eine nachgebaute Börse (`tests/fake_kraken.py`). **Gegen ein echtes Konto lief nichts.**
Einen Starter für echtes Geld gibt es noch nicht.

Zugang prüfen, ohne zu handeln (API-Key ohne Auszahlungsrecht, nur auf dem Gerät eintragen):

    export KRAKEN_API_KEY=...
    export KRAKEN_API_SECRET=...
    python -m tradebot.kraken_check

## Offen

- Starter für echtes Geld, erst nach Paper-Test und Zugangsprüfung.
- Dauerbetrieb: Der Bot braucht ein Gerät, das rund um die Uhr läuft.

## Tests

    cd tradebot && PYTHONPATH=.:tests python -m pytest -q tests
