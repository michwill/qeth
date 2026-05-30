"""Hermetic tests for qeth.simulate — the log-extraction logic.

A fake EVM class is injected so these never fork a real chain. The live
pyrevm-against-mainnet path is exercised manually (it's slow + networked).
"""

from types import SimpleNamespace

from qeth.simulate import simulate_logs

CHAIN = SimpleNamespace(chain_id=1, rpc_url="https://rpc.example/eth")
FROM = "0x7a16ff8270133f063aab6c9977183d9e72835428"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class _FakeLog:
    def __init__(self, address, topics, data_bytes):
        self.address = address
        self.topics = topics
        # pyrevm shape: .data is a (topics, data_bytes) tuple.
        self.data = (topics, data_bytes)


class _FakeEVM:
    """Records construction + the message_call, returns one Transfer."""
    seen: dict = {}

    def __init__(self, fork_url=None):
        _FakeEVM.seen["fork_url"] = fork_url

    def message_call(self, **kwargs):
        _FakeEVM.seen["call"] = kwargs
        self.result = SimpleNamespace(logs=[
            _FakeLog(USDC, [TRANSFER, "0x" + "00" * 31 + "01"],
                     b"\x00" * 31 + b"\x05"),
        ])


def test_returns_decode_ready_log_dicts():
    _FakeEVM.seen = {}
    logs = simulate_logs(CHAIN, FROM, USDC, "0xa9059cbb", 0, evm_cls=_FakeEVM)
    assert _FakeEVM.seen["fork_url"] == CHAIN.rpc_url
    assert len(logs) == 1
    lg = logs[0]
    assert lg["address"] == USDC
    assert lg["topics"][0] == TRANSFER
    assert lg["data"] == "0x" + "00" * 31 + "05"


def test_calldata_and_addresses_are_normalised():
    _FakeEVM.seen = {}
    simulate_logs(CHAIN, FROM, USDC, "0xa9059cbb00ff", 0, evm_cls=_FakeEVM)
    call = _FakeEVM.seen["call"]
    assert call["calldata"] == bytes.fromhex("a9059cbb00ff")
    # web3/pyrevm want checksum addresses — the lowercased inputs are fixed.
    assert call["caller"].lower() == FROM
    assert call["caller"] != FROM           # i.e. it got checksummed
    assert "value" not in call               # zero value omitted


def test_value_is_passed_when_nonzero():
    _FakeEVM.seen = {}
    simulate_logs(CHAIN, FROM, USDC, "0x", 10**18, evm_cls=_FakeEVM)
    assert _FakeEVM.seen["call"]["value"] == 10**18


def test_contract_creation_returns_none():
    assert simulate_logs(CHAIN, FROM, None, "0x", 0, evm_cls=_FakeEVM) is None


def test_simulation_error_returns_none():
    class _Boom:
        def __init__(self, fork_url=None): pass
        def message_call(self, **kw): raise RuntimeError("revm exploded")
    assert simulate_logs(CHAIN, FROM, USDC, "0x", 0, evm_cls=_Boom) is None


# --- revert-reason decoding (pyrevm raises RuntimeError with output bytes) ---

from qeth.simulate import _decode_revert


def test_decode_revert_error_string():
    # Error(string) "ERC20: transfer amount exceeds balance"
    out = ("0x08c379a0"
           "0000000000000000000000000000000000000000000000000000000000000020"
           "0000000000000000000000000000000000000000000000000000000000000026"
           "45524332303a207472616e7366657220616d6f756e7420657863656564732062"
           "616c616e63650000000000000000000000000000000000000000000000000000")
    msg = f"Revert {{ gas_used: 36085, output: {out} }}"
    assert _decode_revert(msg) == "ERC20: transfer amount exceeds balance"


def test_decode_revert_panic():
    msg = ("Revert { output: 0x4e487b71"
           "0000000000000000000000000000000000000000000000000000000000000011 }")
    assert _decode_revert(msg) == "panic 0x11"


def test_decode_revert_no_reason_and_unknown():
    assert _decode_revert("Revert { output: 0x }") == \
        "reverted without a reason string"
    assert "selector 0xdeadbeef" in _decode_revert(
        "Revert { output: 0xdeadbeef }")


def test_rate_limited_retries_then_succeeds():
    # message_call raises a rate-limit twice, then succeeds; the helper
    # should back off (injected no-op sleep) and return the logs.
    class _Flaky:
        attempts = 0
        def __init__(self, fork_url=None): pass
        def message_call(self, **kw):
            _Flaky.attempts += 1
            if _Flaky.attempts < 3:
                raise RuntimeError(
                    'JsonRpcError { code: 15, message: "Too many request" }')
            self.result = SimpleNamespace(logs=[
                _FakeLog(USDC, [TRANSFER], b"\x01")])
    delays = []
    logs = simulate_logs(CHAIN, FROM, USDC, "0x", 0, evm_cls=_Flaky,
                         sleep=delays.append)
    assert _Flaky.attempts == 3        # two failures + one success
    assert len(delays) == 2            # backed off before each retry
    assert logs and len(logs) == 1


def test_rate_limited_gives_up_after_retries():
    class _AlwaysLimited:
        def __init__(self, fork_url=None): pass
        def message_call(self, **kw):
            raise RuntimeError('code: 15, message: "Too many request"')
    logs = simulate_logs(CHAIN, FROM, USDC, "0x", 0, evm_cls=_AlwaysLimited,
                         retries=3, sleep=lambda d: None)
    assert logs is None


def test_revert_is_not_retried():
    # A genuine revert must fail fast — no backoff, single attempt.
    class _Reverter:
        attempts = 0
        def __init__(self, fork_url=None): pass
        def message_call(self, **kw):
            _Reverter.attempts += 1
            raise RuntimeError("Revert { output: 0x }")
    delays = []
    assert simulate_logs(CHAIN, FROM, USDC, "0x", 0, evm_cls=_Reverter,
                         sleep=delays.append) is None
    assert _Reverter.attempts == 1 and delays == []


def test_injected_evm_skips_networked_block_env():
    # With evm_cls injected the helper must not touch the network for a
    # block env — _FakeEVM has no set_block_env and a fork_url to nowhere.
    _FakeEVM.seen = {}
    logs = simulate_logs(CHAIN, FROM, USDC, "0xa9059cbb", 0, evm_cls=_FakeEVM)
    assert logs and "block" not in _FakeEVM.seen   # no set_block_env call
