"""TransactionsWorker retries a transient source error instead of aborting the
whole sparse-sent paging walk on one blip — the _yb.eth "3 of 657" bug: a busy
receive-heavy account's sent txs are sparse, so the view needs many Blockscout
pages, and a single timeout must not zero out the load."""
from qeth.chains import DEFAULT_CHAINS
from qeth.transactions import Transaction, TransactionSourceError
from qeth.plugins.transactions import TransactionsWorker

ADDR = "0x" + "11" * 20


def _tx(nonce: int) -> Transaction:
    return Transaction(
        chain_id=1, hash="0x" + f"{nonce:064x}", block_number=100,
        timestamp=1700, nonce=nonce, from_addr=ADDR, to_addr="0xbeef",
        value_wei=0, gas_used=0, gas_price_wei=10**9, method_id="",
        input_data="0x", success=True, pending=False, raw_signed=None,
    )


class _FlakySource:
    """Raises on the first ``fail_times`` calls, then returns one sent tx."""

    def __init__(self, fail_times: int):
        self.calls = 0
        self.fail_times = fail_times

    def supports(self, chain):
        return True

    def list_transactions(self, chain, address, page=1, limit=50,
                          before_block=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransactionSourceError("transient blip")
        return [_tx(5)]


def _run(worker):
    out: dict = {"fetched": None, "failed": None}
    worker.fetched.connect(lambda *a: out.__setitem__("fetched", a))
    worker.failed.connect(lambda m: out.__setitem__("failed", m))
    worker.run()
    return out


def test_retries_a_transient_blip_then_succeeds(qtbot, monkeypatch):
    monkeypatch.setattr(TransactionsWorker, "RETRY_BACKOFF_S", 0)
    src = _FlakySource(fail_times=2)          # fail twice, succeed on attempt 3
    out = _run(TransactionsWorker(src, DEFAULT_CHAINS[0], ADDR))
    assert src.calls == 3
    assert out["failed"] is None
    assert out["fetched"] is not None         # the page still got emitted


def test_fails_only_after_exhausting_retries(qtbot, monkeypatch):
    monkeypatch.setattr(TransactionsWorker, "RETRY_BACKOFF_S", 0)
    src = _FlakySource(fail_times=99)          # always fails
    out = _run(TransactionsWorker(src, DEFAULT_CHAINS[0], ADDR))
    assert src.calls == TransactionsWorker.MAX_ATTEMPTS
    assert out["fetched"] is None
    assert "transient blip" in (out["failed"] or "")
