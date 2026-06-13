"""DesktopNotifier — argv construction for notify-send / gdbus, offline.

We never spawn a real notifier in tests (it would pop notifications on the
dev's screen); subprocess.Popen is monkeypatched to capture the argv.
"""

import qeth.notify as notify_mod
from qeth.notify import DesktopNotifier


def _recorder(monkeypatch):
    calls: list = []
    monkeypatch.setattr(notify_mod.subprocess, "Popen",
                        lambda argv, **kw: calls.append(argv))
    return calls


def test_no_backend_returns_false(monkeypatch):
    n = DesktopNotifier()
    n._notify_send = None
    n._gdbus = None
    calls = _recorder(monkeypatch)
    assert n.send("Received 5 USDC", "from 0xabc · Ethereum") is False
    assert calls == []


def test_notify_send_argv_without_icon(monkeypatch):
    n = DesktopNotifier()
    n._notify_send = "/usr/bin/notify-send"
    n._gdbus = None
    calls = _recorder(monkeypatch)
    assert n.send("Sent 1 ETH", "to 0xdef · Ethereum") is True
    argv = calls[0]
    assert argv[0] == "/usr/bin/notify-send"
    assert "--app-name=qeth" in argv
    assert "-i" not in argv                          # no pixmap → no icon
    # `--` guards an adversarial summary; payload follows it in order
    assert argv[-3:] == ["--", "Sent 1 ETH", "to 0xdef · Ethereum"]


def test_notify_send_argv_with_icon(monkeypatch, tmp_path, qtbot):
    from PySide6.QtGui import QPixmap
    monkeypatch.setattr(notify_mod, "_ICON_DIR", tmp_path / "notify")
    n = DesktopNotifier()
    n._notify_send = "/usr/bin/notify-send"
    n._gdbus = None
    calls = _recorder(monkeypatch)
    pm = QPixmap(16, 16)
    pm.fill()
    assert n.send("Received 5 USDC", "from 0xabc · Ethereum", pm) is True
    argv = calls[0]
    i = argv.index("-i")
    icon_path = argv[i + 1]
    assert icon_path.endswith(".png")
    assert (tmp_path / "notify").exists()             # icon was written


def test_gdbus_fallback_argv(monkeypatch, tmp_path, qtbot):
    from PySide6.QtGui import QPixmap
    monkeypatch.setattr(notify_mod, "_ICON_DIR", tmp_path / "notify")
    n = DesktopNotifier()
    n._notify_send = None                             # only gdbus available
    n._gdbus = "/usr/bin/gdbus"
    calls = _recorder(monkeypatch)
    pm = QPixmap(16, 16)
    pm.fill()
    assert n.send("Received 5 USDC", "from 0xabc · Ethereum", pm) is True
    argv = calls[0]
    assert argv[0] == "/usr/bin/gdbus" and "call" in argv
    assert "org.freedesktop.Notifications.Notify" in argv
    assert "Received 5 USDC" in argv                  # plain s-typed summary
    # the icon rides an image-path GVariant hint
    assert any("image-path" in a for a in argv)


def test_icon_slots_rotate(monkeypatch, tmp_path, qtbot):
    from PySide6.QtGui import QPixmap
    monkeypatch.setattr(notify_mod, "_ICON_DIR", tmp_path / "notify")
    n = DesktopNotifier()
    n._notify_send = "/usr/bin/notify-send"
    n._gdbus = None
    _recorder(monkeypatch)
    pm = QPixmap(8, 8)
    pm.fill()
    paths = []
    for _ in range(notify_mod._SLOTS + 2):
        n.send("t", "b", pm)
        paths.append(n._slot)
    # bounded ring of slot files, never unbounded
    written = list((tmp_path / "notify").glob("*.png"))
    assert 0 < len(written) <= notify_mod._SLOTS
