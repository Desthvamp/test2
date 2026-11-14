import subprocess
from types import SimpleNamespace

from tradebot.broker import PaperBroker
from tradebot.engine import Engine, EngineConfig
from tradebot.notify import Notifier
from test_engine import dip_and_turn, warm


def notifier(codes=(0,), raises=None, found=True):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if raises:
            raise raises
        return SimpleNamespace(returncode=codes[min(len(calls), len(codes)) - 1])

    n = Notifier("Tradebot (Paper)", run=run, which=lambda name: "/bin/termux-notification" if found else None,
                 background=False)
    return n, calls


def test_sends_android_notification_with_sound_and_vibration():
    n, calls = notifier()
    n.send("Gekauft", "KAUF BTC-EUR 0.000518 @ 57896.20")
    (cmd, kw), = calls
    assert cmd[:5] == ["/bin/termux-notification", "--title", "Tradebot (Paper): Gekauft", "--content",
                       "KAUF BTC-EUR 0.000518 @ 57896.20"]
    assert "--sound" in cmd and "--vibrate" in cmd and cmd[cmd.index("--priority") + 1] == "high"
    assert kw["timeout"] == 15 and n.pop_error() == ""


def test_without_termux_api_nothing_happens():
    n, calls = notifier(found=False)
    assert not n.available
    n.send("Gekauft", "x")
    assert not calls and n.pop_error() == ""


def test_falls_back_to_plain_notification_then_reports_error():
    n, calls = notifier(codes=(1, 0))
    n.send("Gekauft", "x")
    assert len(calls) == 2 and "--sound" not in calls[1][0] and n.pop_error() == ""
    n, calls = notifier(codes=(1, 1))
    n.send("Gekauft", "x")
    assert "fehlgeschlagen" in n.pop_error() and n.pop_error() == ""      # Fehler wird nur einmal gemeldet


def test_hanging_command_is_reported_with_hint():
    n, _ = notifier(raises=subprocess.TimeoutExpired("termux-notification", 15))
    n.send("Gekauft", "x")
    assert "Termux:API installiert" in n.pop_error()


def test_engine_alerts_on_buy_add_and_sell_only(tmp_path):
    alerts = []
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    e = Engine(broker=PaperBroker(cash=100.0), cfg=cfg, on_alert=lambda title, text: alerts.append((title, text)))
    t = warm(e)
    assert not alerts                                        # Kursbewegungen allein melden nichts
    e.tick(101.0, 0.9, now=t, fear_greed=50)
    t = dip_and_turn(e, t + 60)
    e.tick(109.5, 0.9, now=t, fear_greed=50)
    assert [a[0] for a in alerts] == ["Gekauft", "Nachgekauft", "Verkauft"]
    assert alerts[0][1].startswith("KAUF BTC-EUR") and "Ergebnis +" in alerts[2][1]


def test_runner_logs_notification_problems(tmp_path):
    from tradebot.run_live_paper import Runner
    n, _ = notifier(codes=(1, 1))
    cfg = EngineConfig(kill_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "s.json"))
    e = Engine(broker=PaperBroker(cash=100.0), cfg=cfg, on_alert=n.send)
    r = Runner(e, price_fn=lambda: 100.0, news_fn=lambda: (_ for _ in ()).throw(RuntimeError("x")),
               fg_fn=lambda: 50, notifier=n)
    n.send("Test", "x")
    r.step(0.0)
    assert any("Benachrichtigung fehlgeschlagen" in line for line in e.log)
