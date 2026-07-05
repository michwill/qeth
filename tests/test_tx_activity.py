"""Unit tests for tx_activity.transfer_legs_from_logs — turning a tx's
event logs (receipt or pre-broadcast simulation) into the ERC-20 contracts
the viewer sent / received, which the Activity column folds in so a swap's
coins show before Blockscout indexes the transfers."""

from qeth.tx_activity import TRANSFER_TOPIC0, transfer_legs_from_logs

VIEWER = "0x7a16ff8270133f063aab6c9977183d9e72835428"
OTHER = "0x" + "11" * 20
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WBTC = "0x" + "22" * 20


def _transfer(token: str, frm: str, to: str) -> dict:
    """An ERC-20 Transfer log: addresses are 32-byte left-padded topics."""
    return {
        "topics": [
            TRANSFER_TOPIC0,
            "0x" + "00" * 12 + frm[2:].lower(),
            "0x" + "00" * 12 + to[2:].lower(),
        ],
        "data": "0x" + "00" * 31 + "64",   # value (irrelevant to legs)
        "address": token,
    }


def test_received_token_is_an_in_leg():
    assert transfer_legs_from_logs([_transfer(USDC, OTHER, VIEWER)], VIEWER) \
        == ([], [USDC])


def test_sent_token_is_an_out_leg():
    assert transfer_legs_from_logs([_transfer(USDC, VIEWER, OTHER)], VIEWER) \
        == ([USDC], [])


def test_swap_shows_both_sides():
    logs = [_transfer(USDC, VIEWER, OTHER), _transfer(WBTC, OTHER, VIEWER)]
    assert transfer_legs_from_logs(logs, VIEWER) == ([USDC], [WBTC])


def test_duplicates_collapse_and_untouched_ignored():
    other2 = "0x" + "33" * 20
    logs = [
        _transfer(USDC, OTHER, VIEWER),
        _transfer(USDC, OTHER, VIEWER),     # same token again → deduped
        _transfer(WBTC, OTHER, other2),     # never touches the viewer
    ]
    assert transfer_legs_from_logs(logs, VIEWER) == ([], [USDC])


def test_viewer_match_is_case_insensitive():
    out, inn = transfer_legs_from_logs(
        [_transfer(USDC, OTHER, VIEWER)], VIEWER.upper())
    assert inn == [USDC]


def test_non_transfer_logs_skipped():
    log = {
        "topics": ["0x" + "ab" * 32, "0x" + "00" * 32, "0x" + "00" * 32],
        "data": "0x",
        "address": USDC,
    }
    assert transfer_legs_from_logs([log], VIEWER) == ([], [])


def test_empty_or_none_logs():
    assert transfer_legs_from_logs(None, VIEWER) == ([], [])
    assert transfer_legs_from_logs([], VIEWER) == ([], [])


# --- pass 0: the method verb paints before the (slow) coins fetch -----------

def _tx(contract, method_id="0xa9059cbb"):
    from qeth.transactions import Transaction
    return Transaction(
        chain_id=1, hash="0x" + "cd" * 32, block_number=100, timestamp=0,
        nonce=0, from_addr=VIEWER, to_addr=contract, value_wei=0,
        gas_used=0, gas_price_wei=0, method_id=method_id, input_data="0x",
        success=True, pending=True,
    )


_TRANSFER_ABI = [{
    "type": "function", "name": "transfer",
    "inputs": [{"name": "_to", "type": "address"},
               {"name": "_value", "type": "uint256"}],
    "outputs": [],
}]


def test_verb_emitted_before_coins_fetch(tmp_path, monkeypatch):
    """A cached-ABI method label needs no network, so it must paint BEFORE the
    tokentx/internal fetch — otherwise a just-created pending tx shows a blank
    method until that (possibly slow) round-trip returns. Regression for the
    'method appears only after navigating away and back' report."""
    import qeth.tx_activity as ta
    from qeth.abi_cache import AbiCache
    from qeth.chains import DEFAULT_CHAINS

    chain = next(c for c in DEFAULT_CHAINS if c.chain_id == 1)
    contract = "0x" + "ab" * 20
    cache = AbiCache(root=tmp_path)
    cache.save(1, contract, _TRANSFER_ABI)

    order: list[str] = []

    def slow_rows(*a, **k):
        order.append("coins_fetch")        # stands in for the slow network call
        return []
    monkeypatch.setattr(ta, "_account_rows", slow_rows)

    batches: list[dict] = []

    def on_batch(b):
        order.append("emit")
        batches.append(b)

    tx = _tx(contract)
    ta.fetch_activities(chain, VIEWER, [tx], abi_cache=cache, on_batch=on_batch)

    # the verb was emitted before the coins fetch ran...
    assert order[0] == "emit"
    assert order.index("emit") < order.index("coins_fetch")
    # ...carrying the decoded method name, not a blank or bare selector
    assert batches[0][tx.hash].verb == "transfer"


def test_pass0_emits_known_verb_but_not_cold_placeholder(tmp_path, monkeypatch):
    """Pass 0 emits only verbs resolvable without network: a cached-ABI tx is
    named immediately, but a cold-ABI tx must NOT get a bare-selector
    placeholder there (that risks persisting a non-final row) — it waits for
    the pass-2 fetch."""
    import qeth.tx_activity as ta
    from qeth.abi_cache import AbiCache
    from qeth.chains import DEFAULT_CHAINS
    from qeth.transactions import Transaction

    chain = next(c for c in DEFAULT_CHAINS if c.chain_id == 1)
    known_c = "0x" + "ab" * 20
    cold_c = "0x" + "ef" * 20
    cache = AbiCache(root=tmp_path)
    cache.save(1, known_c, _TRANSFER_ABI)           # only the known one cached
    monkeypatch.setattr(ta, "_account_rows", lambda *a, **k: [])
    monkeypatch.setattr(ta._Verbs, "resolve", lambda self, c: {})   # no network

    known = _tx(known_c)
    cold = Transaction(
        chain_id=1, hash="0x" + "ee" * 32, block_number=100, timestamp=0,
        nonce=1, from_addr=VIEWER, to_addr=cold_c, value_wei=0, gas_used=0,
        gas_price_wei=0, method_id="0xdeadbeef", input_data="0x", success=True)

    batches: list[dict] = []
    ta.fetch_activities(chain, VIEWER, [known, cold], abi_cache=cache,
                        on_batch=batches.append)

    # First (pass-0) batch: the cached verb, and NOT the cold tx.
    assert batches[0][known.hash].verb == "transfer"
    assert cold.hash not in batches[0]


def _transfer_full(token, frm, to, value, tx_hash, log_index):
    """A Transfer log carrying the fields the notification path reads."""
    log = _transfer(token, frm, to)
    log["data"] = "0x" + f"{value:064x}"
    log["transactionHash"] = tx_hash
    log["logIndex"] = log_index
    return log


def test_transfers_touching_extracts_value_counterparty_and_position():
    from qeth.tx_activity import transfers_touching
    logs = [
        _transfer_full(USDC, OTHER, VIEWER, 2_500_000, "0xDEAD", "0x3"),  # in, hex idx
        _transfer_full(WBTC, VIEWER, OTHER, 10 ** 8, "0xDEAD", 4),        # out, int idx
    ]
    rows = transfers_touching(logs, VIEWER)
    assert [(r.token, r.counterparty, r.outgoing, r.value, r.tx_hash, r.log_index)
            for r in rows] == [
        (USDC, OTHER, False, 2_500_000, "0xdead", 3),
        (WBTC, OTHER, True, 10 ** 8, "0xdead", 4),
    ]


def test_transfers_touching_skips_untouched_and_malformed():
    from qeth.tx_activity import transfers_touching
    third = "0x" + "33" * 20
    logs = [
        _transfer_full(USDC, OTHER, third, 1, "0x1", 0),   # between others → skip
        {"topics": [TRANSFER_TOPIC0], "address": USDC},    # malformed (short topics)
    ]
    assert transfers_touching(logs, VIEWER) == []


# --- pass 3: native-in fallback via node trace (TOKEN->native swap) ----------
#
# A TOKEN->ETH swap unwraps WETH and receives the native ETH by an internal tx.
# Blockscout's internal-tx index lags that — minutes on mainnet, and on L2s the
# row can stay one-sided indefinitely — so the swap shows "OP ->" with nothing
# in. fetch_activities recovers the received-ETH leg from the node's callTracer.

OP = "0x4200000000000000000000000000000000000042"
ROUTER = "0x0dcded3545d565ba3b19e683431381007245d983"


def _swap_tx(method_id="0x5c9c18e2", hash_="0x" + "ab" * 32):
    """A viewer-initiated contract call (a swap) carrying no native value."""
    from qeth.transactions import Transaction
    return Transaction(
        chain_id=10, hash=hash_, block_number=200, timestamp=0, nonce=0,
        from_addr=VIEWER, to_addr=ROUTER, value_wei=0, gas_used=0,
        gas_price_wei=0, method_id=method_id, input_data="0x", success=True)


def _tokentx(sym, contract, frm, to, tx_hash):
    return {"hash": tx_hash, "contractAddress": contract, "tokenSymbol": sym,
            "from": frm, "to": to}


def _op_chain():
    from qeth.chains import DEFAULT_CHAINS
    return next(c for c in DEFAULT_CHAINS if c.chain_id == 10)


def _rows_op_out(tx):
    """_account_rows stub: OP leaves the viewer (tokentx), no internals — the
    one-sided shape a TOKEN->ETH swap has while the internal index is behind."""
    def rows(base, action, address, timeout, **k):
        if action == "tokentx":
            return [_tokentx("OP", OP, VIEWER, ROUTER, tx.hash)]
        return []
    return rows


def test_trace_capable_chain_prefers_bundled_endpoint():
    """The user may point Optimism at mainnet.optimism.io, which refuses
    debug_traceTransaction (-32601). The trace must route to the bundled
    default (DRPC) first, with the user's RPC kept only as a fallback."""
    from dataclasses import replace
    from qeth.chains import DEFAULT_CHAINS
    from qeth.tx_activity import _trace_capable_chain

    default = next(c for c in DEFAULT_CHAINS if c.chain_id == 10)
    user = replace(default, rpc_url="https://mainnet.optimism.io",
                   fallback_rpcs=())
    routed = _trace_capable_chain(user)
    # DRPC (the bundled default) leads; the user's RPC is a fallback, not dropped
    assert routed.rpc_url == default.rpc_url
    assert "https://mainnet.optimism.io" in routed.fallback_rpcs
    assert routed.rpc_url not in routed.fallback_rpcs   # no dupes


def test_trace_capable_chain_unknown_chain_keeps_user_rpc():
    """A chain with no bundled default (added via chainlist) keeps the user's
    RPC — there's no known-capable endpoint to prefer."""
    from qeth.chains import Chain
    from qeth.tx_activity import _trace_capable_chain

    custom = Chain(name="Custom", chain_id=999999, rpc_url="https://custom.example",
                   symbol="X")
    routed = _trace_capable_chain(custom)
    assert routed.rpc_url == "https://custom.example"


def test_native_in_from_trace_sums_viewer_calls():
    from qeth.tx_activity import native_in_from_trace
    frame = {
        "from": VIEWER, "to": ROUTER, "value": "0x0", "calls": [
            {"from": ROUTER, "to": OTHER, "value": "0x64"},         # not the viewer
            {"from": ROUTER, "to": VIEWER, "value": "0x56", "calls": [
                {"from": VIEWER, "to": ROUTER, "value": "0x1"},     # viewer's own out
            ]},
        ],
    }
    # only the 0x56 credited TO the viewer counts (0x64 → other; 0x1 → viewer-out)
    assert native_in_from_trace(frame, VIEWER) == 0x56


def test_native_in_from_trace_skips_reverted_subtree():
    from qeth.tx_activity import native_in_from_trace
    frame = {"from": VIEWER, "to": ROUTER, "value": "0x0", "calls": [
        {"from": ROUTER, "to": VIEWER, "value": "0x99", "error": "reverted",
         "calls": [{"from": ROUTER, "to": VIEWER, "value": "0x5"}]},
    ]}
    assert native_in_from_trace(frame, VIEWER) == 0


def test_one_sided_swap_gains_native_leg_from_trace(tmp_path, monkeypatch):
    """OP out, nothing from the internal-tx index → read the received ETH from
    the trace and show it: "OP ->" becomes "OP -> ETH"."""
    import qeth.tx_activity as ta
    from qeth.abi_cache import AbiCache
    from qeth.tx_activity import AssetLeg

    tx = _swap_tx()
    monkeypatch.setattr(ta, "_account_rows", _rows_op_out(tx))
    monkeypatch.setattr(ta._Verbs, "resolve", lambda self, c: {})   # no network

    result = ta.fetch_activities(
        _op_chain(), VIEWER, [tx], abi_cache=AbiCache(root=tmp_path),
        trace_native_in=lambda h: 24252677217318157)

    assert result[tx.hash].out == (AssetLeg("OP", OP),)
    assert result[tx.hash].inn == (AssetLeg("ETH", None),)


def test_swap_with_zero_trace_stays_one_sided(tmp_path, monkeypatch):
    """No internal ETH to the viewer (trace returns 0) → no phantom ETH leg."""
    import qeth.tx_activity as ta
    from qeth.abi_cache import AbiCache
    from qeth.tx_activity import AssetLeg

    tx = _swap_tx()
    monkeypatch.setattr(ta, "_account_rows", _rows_op_out(tx))
    monkeypatch.setattr(ta._Verbs, "resolve", lambda self, c: {})

    result = ta.fetch_activities(
        _op_chain(), VIEWER, [tx], abi_cache=AbiCache(root=tmp_path),
        trace_native_in=lambda h: 0)

    assert result[tx.hash].out == (AssetLeg("OP", OP),)
    assert result[tx.hash].inn == ()


def test_two_sided_swap_is_not_traced(tmp_path, monkeypatch):
    """A token->token swap already shows an in-leg, so it must NOT trigger a
    trace — otherwise every ordinary swap re-traces for nothing."""
    import qeth.tx_activity as ta
    from qeth.abi_cache import AbiCache

    tx = _swap_tx()

    def rows(base, action, address, timeout, **k):
        if action == "tokentx":
            return [_tokentx("OP", OP, VIEWER, ROUTER, tx.hash),
                    _tokentx("WBTC", WBTC, ROUTER, VIEWER, tx.hash)]
        return []
    monkeypatch.setattr(ta, "_account_rows", rows)
    monkeypatch.setattr(ta._Verbs, "resolve", lambda self, c: {})

    calls: list[str] = []
    ta.fetch_activities(_op_chain(), VIEWER, [tx],
                        abi_cache=AbiCache(root=tmp_path),
                        trace_native_in=lambda h: calls.append(h) or 1)
    assert calls == []


def test_plain_transfer_is_not_traced(tmp_path, monkeypatch):
    """A plain ERC-20 transfer is one-sided too, but it's a send, not a swap —
    it must not be traced (nor an approve)."""
    import qeth.tx_activity as ta
    from qeth.abi_cache import AbiCache

    tx = _swap_tx(method_id="0xa9059cbb")     # transfer(address,uint256)
    monkeypatch.setattr(ta, "_account_rows", _rows_op_out(tx))
    monkeypatch.setattr(ta._Verbs, "resolve", lambda self, c: {})

    calls: list[str] = []
    ta.fetch_activities(_op_chain(), VIEWER, [tx],
                        abi_cache=AbiCache(root=tmp_path),
                        trace_native_in=lambda h: calls.append(h) or 1)
    assert calls == []
