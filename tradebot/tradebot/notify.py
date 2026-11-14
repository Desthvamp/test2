"""Benachrichtigungen auf dem Android-Gerät, auf dem der Bot in Termux läuft.

Braucht die App "Termux:API" (aus F-Droid, gleiche Quelle wie Termux) und in Termux einmal:
    pkg install termux-api
Testen:
    python -m tradebot.notify

Ohne Termux:API läuft der Bot normal weiter, nur ohne Benachrichtigungen.
Auf einem echten Android-Gerät ist das hier NICHT getestet; geprüft ist nur, welcher Befehl
mit welchen Angaben aufgerufen wird."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time


class Notifier:
    def __init__(self, prefix: str = "Tradebot", run=subprocess.run, which=shutil.which, background: bool = True):
        self.prefix, self.run, self.background = prefix, run, background
        self.cmd = which("termux-notification")
        self._error = ""
        self._n = 0

    @property
    def available(self) -> bool:
        return bool(self.cmd)

    def pop_error(self) -> str:
        """Letzter Fehler seit dem vorigen Aufruf (leer, wenn alles gut ging)."""
        err, self._error = self._error, ""
        return err

    def send(self, title: str, text: str) -> None:
        """Kehrt sofort zurück; ein hängender Befehl darf den Handel nicht aufhalten."""
        if not self.cmd:
            return
        if self.background:
            threading.Thread(target=self._send, args=(title, text), daemon=True).start()
        else:
            self._send(title, text)

    def _send(self, title: str, text: str) -> None:
        self._n += 1
        base = [self.cmd, "--title", f"{self.prefix}: {title}", "--content", text]
        extras = ["--priority", "high", "--sound", "--vibrate", "300,150,300",
                  "--group", "tradebot", "--id", f"tradebot-{int(time.time())}-{self._n}"]
        try:
            r = self.run(base + extras, timeout=15, capture_output=True)
            if r.returncode != 0:      # ältere Version kennt eine Angabe nicht: schlicht noch einmal
                r = self.run(base, timeout=15, capture_output=True)
            if r.returncode != 0:
                self._error = f"Benachrichtigung fehlgeschlagen (Fehlercode {r.returncode})"
        except subprocess.TimeoutExpired:
            self._error = "Benachrichtigung hängt. Ist die App Termux:API installiert?"
        except OSError as e:
            self._error = f"Benachrichtigung fehlgeschlagen ({e})"


def main() -> int:
    n = Notifier(background=False)
    if not n.available:
        print("Der Befehl termux-notification fehlt. In Termux ausführen: pkg install termux-api")
        print("Außerdem die App Termux:API aus F-Droid installieren.")
        return 2
    n.send("Test", "Wenn du das siehst, funktionieren die Benachrichtigungen.")
    err = n.pop_error()
    print(err or "Gesendet. Jetzt sollte eine Benachrichtigung erscheinen.")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
