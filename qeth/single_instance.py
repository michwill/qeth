"""Single-instance guard (P4 4a).

Two qeth processes sharing ``~/.qeth`` silently clobber each other's config,
wallet cache and tx cache: every store is load-once + whole-state-save with no
cross-process merge, so the later saver drops whatever the other added
(permanently — a pending tx's rebroadcast bytes, an added account, a
receipt-credited token). A ``QLocalServer`` / ``QLocalSocket`` rendezvous makes
a second launch hand off to the running instance (raise its window) and exit,
so only one process ever writes the store.

Fails OPEN: if the guard can't be established (a listen hiccup, a platform
without QtNetwork sockets), qeth still starts — the guard is a safeguard, not a
gate.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("qeth.single_instance")


def _server_name(key: str) -> str:
    """A local-socket name derived from ``key`` (the config root). Instances
    that share the root collide; instances with different roots don't. Hashed
    (a socket name isn't a filesystem path) and salted with the uid so two
    users on one host never share a rendezvous."""
    salt = str(os.getuid()) if hasattr(os, "getuid") else "0"
    digest = hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()[:16]
    return f"qeth-{digest}"


class SingleInstanceGuard(QObject):
    """Owns the single-instance rendezvous. Call :meth:`is_primary` once at
    startup; if it returns False another instance is running (already asked to
    raise its window) and the caller should exit. The primary keeps the guard
    alive for the process lifetime and calls :meth:`set_window` so an incoming
    hand-off can raise the real window."""

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = _server_name(key)
        self._server: QLocalServer | None = None
        self._window: Any = None

    def is_primary(self, connect_timeout_ms: int = 500) -> bool:
        """True if we claimed the single-instance slot. False if another
        instance already holds it — we poke it to raise its window first, so the
        caller can just exit."""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if sock.waitForConnected(connect_timeout_ms):
            # A live primary is listening — ask it to surface, then bow out.
            # The connection alone is the signal; the payload is a courtesy.
            sock.write(b"raise\n")
            sock.flush()
            sock.waitForBytesWritten(connect_timeout_ms)
            sock.disconnectFromServer()
            return False
        # Nobody listening. A crashed primary may have left a stale socket that
        # would fail listen() with AddressInUseError — clear it, then claim.
        # (Safe: we only reach here because connect FAILED, i.e. no live server;
        # a live one accepts instantly, well within the timeout.)
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        if not server.listen(self._name):
            # Couldn't claim the slot — fail OPEN (start anyway) rather than
            # refuse to launch over a guard hiccup. Worst case: no guard, i.e.
            # the pre-4a behaviour.
            log.warning("single-instance listen failed: %s", server.errorString())
            return True
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def set_window(self, window: Any) -> None:
        """The window a hand-off should raise (set once the UI exists)."""
        self._window = window

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        conn = self._server.nextPendingConnection()
        if conn is not None:
            conn.close()   # a connection means "raise"; we don't need the bytes
        self._raise_window()

    def _raise_window(self) -> None:
        win = self._window
        if win is None:
            return
        # The tray-restore pattern (tray.py): showNormal() maps AND clears the
        # minimised bit in one documented call — no setWindowState, which can
        # hang on a not-yet-mapped window.
        win.showNormal()
        win.raise_()
        win.activateWindow()
