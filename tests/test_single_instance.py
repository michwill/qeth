"""Single-instance guard (P4 4a): a second launch detects the primary, asks it
to raise its window, and reports itself non-primary so the caller can exit."""
from qeth.single_instance import SingleInstanceGuard, _server_name


def test_server_name_stable_per_key_and_uid_salted():
    # Same key → same name (so instances sharing a config root rendezvous);
    # different keys → different names (separate roots don't coalesce).
    assert _server_name("/home/u/.qeth") == _server_name("/home/u/.qeth")
    assert _server_name("/home/u/.qeth") != _server_name("/tmp/other/.qeth")
    assert _server_name("/home/u/.qeth").startswith("qeth-")


def test_second_instance_is_not_primary_and_raises_the_first(qtbot, tmp_path):
    key = str(tmp_path)                      # unique rendezvous per test run
    g1 = SingleInstanceGuard(key)
    try:
        assert g1.is_primary() is True       # first claims the slot

        raised = []

        class _Win:
            def showNormal(self):
                raised.append("normal")

            def raise_(self):
                raised.append("raise")

            def activateWindow(self):
                raised.append("activate")

        g1.set_window(_Win())

        g2 = SingleInstanceGuard(key)
        assert g2.is_primary() is False      # second detects the first, bows out

        # the first raises its window on the incoming hand-off (event-loop driven)
        qtbot.waitUntil(lambda: bool(raised), timeout=2000)
        assert raised == ["normal", "raise", "activate"]
    finally:
        if g1._server is not None:
            g1._server.close()


def test_primary_without_a_window_does_not_crash_on_handoff(qtbot, tmp_path):
    # A hand-off arriving before set_window() (a launch during startup) must be
    # a no-op, not a crash.
    key = str(tmp_path)
    g1 = SingleInstanceGuard(key)
    try:
        assert g1.is_primary() is True
        g2 = SingleInstanceGuard(key)
        assert g2.is_primary() is False
        qtbot.wait(200)                      # let the connection be processed
    finally:
        if g1._server is not None:
            g1._server.close()
