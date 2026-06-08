"""Integration tests for the ws live watcher against a local anvil fork.

Anvil forks mainnet (real token contracts + whale balances) and serves a real
ws JSON-RPC on localhost, so the watcher exercises its *actual* ws /
eth_subscribe path — but the events are deterministic because we trigger them
ourselves (impersonate → transfer → mine). This is the robust counterpart to
the flaky, rate-limited live-RPC checks: we control exactly which blocks and
logs happen.

Marked ``network`` (forking needs an upstream RPC for state); skipped cleanly
when anvil isn't installed or the fork is unreachable. Override the fork RPC
with ``QETH_ANVIL_FORK_RPC``.
"""

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request

import pytest

from qeth.chains import Chain
from qeth.live_watcher import LiveWatcher, PendingTx

FORK_RPC = os.environ.get("QETH_ANVIL_FORK_RPC",
                          "https://ethereum-rpc.publicnode.com")
USDC  = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WHALE = "0x28C6c06298d514Db089934071355E5743bf21d60"   # Binance 14 (USDC + ETH)
ACCT  = "0x1111111111111111111111111111111111111111"   # the watched account
ANY   = "0x2222222222222222222222222222222222222222"


def _pad(addr: str) -> str:
    return addr[2:].lower().rjust(64, "0")


class _Anvil:
    def __init__(self, port: int):
        self.http = f"http://127.0.0.1:{port}"
        self.chain = Chain("AnvilFork", 1, self.http,
                           ws_url=(f"ws://127.0.0.1:{port}",))

    def rpc(self, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "method": method, "params": params or []}).encode()
        req = urllib.request.Request(
            self.http, data=body, headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if resp.get("error"):
            raise RuntimeError(resp["error"])
        return resp["result"]

    def wait_ready(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                self.rpc("eth_blockNumber")
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def mine(self):
        self.rpc("evm_mine")

    def impersonate(self, addr):
        self.rpc("anvil_impersonateAccount", [addr])
        self.rpc("anvil_setBalance", [addr, hex(10 ** 18)])   # gas

    def send(self, frm, to, data="0x"):
        return self.rpc("eth_sendTransaction",
                        [{"from": frm, "to": to, "data": data}])

    def erc20_balance(self, token, holder):
        return int(self.rpc("eth_call",
            [{"to": token, "data": "0x70a08231" + _pad(holder)}, "latest"]), 16)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def anvil():
    """A forked-mainnet anvil with ws + manual mining (so the test controls
    exactly when blocks happen)."""
    if not shutil.which("anvil"):
        pytest.skip("anvil not installed")
    port = _free_port()
    proc = subprocess.Popen(
        ["anvil", "--fork-url", FORK_RPC, "--port", str(port),
         "--no-mining", "--silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    a = _Anvil(port)
    try:
        if not a.wait_ready(40):
            pytest.skip(f"anvil fork unreachable ({FORK_RPC})")
        yield a
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.network
def test_ws_captures_transfer_log_as_balance_dirty(anvil, qtbot):
    """A real ERC-20 Transfer to the watched account, mined on the fork, is
    captured by the logs subscription and surfaced as balance_dirty."""
    if anvil.erc20_balance(USDC, WHALE) < 10 ** 6:
        pytest.skip("whale lacks USDC at this fork block")
    dirty: list = []
    up: list = []
    w = LiveWatcher(lambda: [anvil.chain],
                    account_provider=lambda: (anvil.chain, ACCT))
    w.balance_dirty.connect(lambda c, a, t: dirty.append(t.lower()))
    w.link_state.connect(lambda c, on: up.append(on) if on else None)
    w.start()
    try:
        qtbot.waitUntil(lambda: bool(up), timeout=10_000)   # connected + subscribed
        anvil.impersonate(WHALE)
        anvil.send(WHALE, USDC,
                   "0xa9059cbb" + _pad(ACCT) + hex(10 ** 6)[2:].rjust(64, "0"))
        anvil.mine()
        qtbot.waitUntil(lambda: USDC.lower() in dirty, timeout=10_000)
    finally:
        w.stop()
    assert USDC.lower() in dirty


@pytest.mark.network
def test_ws_confirms_pending_tx_on_mine(anvil, qtbot):
    """A pending tx confirms via the newHeads-driven receipt probe the moment
    its block is mined on the fork."""
    anvil.impersonate(WHALE)
    txhash = anvil.send(WHALE, ANY, "0x")             # pending (no-mining)
    pending = [PendingTx(txhash, WHALE, 0, None)]
    confirmed: list = []
    up: list = []
    w = LiveWatcher(lambda: [anvil.chain], pending_provider=lambda cid: pending)
    w.confirmed.connect(lambda c, h, r: confirmed.append(h))
    w.link_state.connect(lambda c, on: up.append(on) if on else None)
    w.start()
    try:
        qtbot.waitUntil(lambda: bool(up), timeout=10_000)
        anvil.mine()                                  # tx mines -> newHead -> probe
        qtbot.waitUntil(lambda: txhash in confirmed, timeout=10_000)
    finally:
        w.stop()
    assert txhash in confirmed
