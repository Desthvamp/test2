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

Eine feste Pause nach einem Verkauf gibt es nicht. Das Kaufsignal muss nach einem Verkauf aber einmal
erlöschen, bevor wieder gekauft wird; sonst würde der Bot im selben Moment zum selben Kurs zurückkaufen.

Verkauft wird bei Gewinnmitnahme (+10 %), nachgezogenem Stop (ab +6 % Gewinn folgt ein Stop dem Kurs mit
3 % Abstand) oder Not-Aus (Datei `KILL` im Ordner `tradebot` anlegen).

**Es gibt keinen Stop-Loss.** Der Bot verkauft nie mit Verlust, sondern hält die Position, bis sie wieder im
Gewinn ist. Der Verlust einer Position ist damit nicht begrenzt, und solange sie im Minus ist, macht der Bot
außer einem Nachkauf nichts. Ein Stop-Loss lässt sich wieder einschalten (`stop_loss_pct` in `risk.py`).

Nachkauf: Liegt der Kurs mindestens 3 % unter dem Einstieg und steigt wieder (höher als vor 15 Minuten), kauft
der Bot einmal für denselben Betrag nach. Ziel und Trailing gelten danach ab dem Durchschnittskurs.

Einsatz (`risk.py`): 30 % des Kapitals pro Kauf, mit Nachkauf also bis zu rund 60 %. Ab −5 % Tagesverlust keine
neuen Käufe und keine Nachkäufe. Gebühr im Paper-Modus: 0,8 % je Kauf und Verkauf (Kraken, niedrigster Tarif,
Stand November 2026).

**Alle Schwellen sind Faustwerte und nicht an historischen Daten geprüft.** Die Nachrichten-Stimmung ist eine
einfache Schlüsselwort-Zählung in englischen Schlagzeilen, keine KI.

## Datenquellen

Kurs: Kraken (öffentlich). Stimmung: alternative.me Fear & Greed. Nachrichten: RSS von Cointelegraph und CoinDesk.
Kein Kurs: Tick wird übersprungen. Keine Nachrichten: es wird nichts gekauft, offene Positionen werden weiter überwacht.

## Kraken (für später, echtes Geld)

`kraken.py` ist nach der offiziellen Kraken-Doku gebaut:

- Kauf als Limit-Order, die sofort ausgeführt wird oder verfällt (höchstens 0,5 % über dem aktuellen Kurs).
- Ein Stop liegt als echte Order bei Kraken und wird dort nachgezogen. Er greift auch, wenn der Bot oder
  das Gerät ausfällt. In der Standardeinstellung entsteht er erst im Gewinn; einen Stop-Loss gibt es nicht.
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
