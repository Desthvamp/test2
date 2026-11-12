# Tradebot (Paper-Modus)

Autonomer Trading-Bot-Grundgeruest. **Standard ist Paper-Trading** (simuliertes Geld).
Live-Handel ist nicht implementiert.

## Was schon drin ist
- Positionsgroesse: maximal 2 % Verlust pro Trade, max. 50 % des Kapitals in einer Position
- Stop-Loss, Take-Profit, Trailing-Stop (wandert nur nach oben, sichert Gewinne)
- Tageslimit: ab -5 % Tagesverlust keine neuen Trades mehr
- Kill-Switch: Datei `KILL` anlegen -> keine neuen Trades, offene Positionen werden geschlossen
- Nachrichten-Stimmung per Schluesselwoerter (`news.py`) – einfach, keine KI, kein Heilsversprechen
- Dashboard `dashboard.html` (liest `state.json`, im Browser des Tablets oeffnen)

## Noch offen (bewusst)
- Echte Kursdaten und echte Nachrichten-Feeds: `run_paper.py` nutzt aktuell Zufallskurse
- Bitpanda-Anbindung: `BitpandaBroker` wirft absichtlich `NotImplementedError`. Die Bitpanda-API-Doku
  war aus der Entwicklungsumgebung nicht erreichbar; sie muss gegen die offizielle Doku geprueft werden.
- Android: Das Dashboard ist eine Web-Seite, die man auf dem Tablet aufrufen kann. Eine native App ist nicht gebaut.
- Dauerbetrieb: Der Bot muss auf einem Rechner/Server laufen, der 24/7 an ist. Eine Sandbox-Session
  reicht dafuer nicht.

## Tests
    cd tradebot && PYTHONPATH=. python -m pytest -q tests
