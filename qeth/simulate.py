"""Local transaction simulation via revm (pyrevm).

Run a not-yet-broadcast transaction against the chain's *forked* latest
state and return the event logs it would emit — so the Send / dapp-Sign
dialogs can preview "what will happen" the same way the confirmed-tx
details view shows past events.

Forking only uses standard state-read RPC methods
(``eth_getStorageAt`` / ``eth_getCode`` / ``eth_getBalance`` …), which
every endpoint supports — unlike ``debug_traceCall`` / ``eth_simulateV1``
which many public RPCs don't expose.

**Block environment.** pyrevm forks *state* at the latest block but
leaves the block environment zeroed (``block.timestamp == 1``,
``number == 0``, ``basefee == 0``). Any contract that does time math —
oracle staleness checks, swap/permit deadlines, TWAP windows — then
reverts (``block.timestamp - storedTimestamp`` underflows under Solidity
0.8 checked math). So we fetch the real latest block and set the block
env from it before the call; without this, perfectly valid txs simulate
as reverting. See the ``_latest_block`` / block-env wiring below.

``pyrevm`` is an **optional** dependency: when it isn't installed (or the
simulation errors) the helpers return ``None`` and callers simply skip
the preview. The simulation is slow-ish — each cold storage slot is an
RPC round-trip — so callers should run it off the main thread.
"""

import logging
import re
import time

from eth_utils import to_checksum_address

log = logging.getLogger("qeth.simulate")

# Standard Solidity revert envelopes: Error(string) and Panic(uint256).
_ERROR_SELECTOR = "08c379a0"
_PANIC_SELECTOR = "4e487b71"
# pyrevm raises RuntimeError("Revert { gas_used: N, output: 0x.. }") on a
# reverting call; pull the output payload back out to decode the reason.
_REVERT_OUTPUT_RE = re.compile(r"output:\s*(0x[0-9a-fA-F]*)")


def pyrevm_available() -> bool:
    """True if the optional ``pyrevm`` dependency can be imported."""
    try:
        import pyrevm  # noqa: F401
        return True
    except Exception:
        return False


def _hexint(v):
    """JSON-RPC quantities come back as ``0x``-hex strings (raw) or, if a
    web3 result formatter ran, as plain ints. Accept either."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    return int(v, 16)


def _is_rate_limited(e) -> bool:
    """True when an exception looks like an RPC rate-limit response.
    Forking issues one request per cold storage slot; on a throttled
    public endpoint (DRPC free tier: ``code 15 "Too many request"`` /
    HTTP 429) the burst can trip the limit, especially while the app is
    also polling. These are transient — worth a backoff + retry, unlike a
    genuine revert."""
    s = str(e).lower()
    return ("too many request" in s or "code: 15" in s
            or "429" in s or "rate limit" in s)


def _latest_block(chain):
    """Fetch the latest block's env-relevant fields (as ints / address
    string) so the simulation runs in a realistic block context. Raises
    on RPC failure — the retry loop in ``simulate_logs`` handles transient
    rate-limits; other errors abort the preview (no env-less fork, which
    would reintroduce the zeroed-timestamp false reverts)."""
    from .chain import EthClient
    blk = EthClient(chain).rpc("eth_getBlockByNumber", ["latest", False])
    if not blk:
        return None
    return {
        "number": _hexint(blk["number"]),
        "timestamp": _hexint(blk["timestamp"]),
        "basefee": _hexint(blk.get("baseFeePerGas")) or 0,
        "gas_limit": _hexint(blk.get("gasLimit")) or 0,
        "coinbase": blk.get("miner"),
    }


def _apply_block_env(evm, block) -> None:
    from pyrevm import BlockEnv
    kwargs = {
        "number": block["number"],
        "timestamp": block["timestamp"],
        "basefee": block["basefee"],
    }
    if block.get("gas_limit"):
        kwargs["gas_limit"] = block["gas_limit"]
    if block.get("coinbase"):
        kwargs["coinbase"] = to_checksum_address(block["coinbase"])
    evm.set_block_env(BlockEnv(**kwargs))


def _decode_revert(msg: str) -> str:
    """Best-effort human revert reason from pyrevm's RuntimeError text,
    which embeds the call's ``output:`` bytes. Decodes the standard
    ``Error(string)`` / ``Panic(uint256)`` envelopes; falls back to the
    raw selector (or the original message) when it's something else."""
    m = _REVERT_OUTPUT_RE.search(msg)
    if not m:
        return msg.strip()
    out = m.group(1)[2:]
    if not out:
        return "reverted without a reason string"
    selector, payload = out[:8], out[8:]
    if selector == _ERROR_SELECTOR:
        try:
            from eth_abi import decode
            (reason,) = decode(["string"], bytes.fromhex(payload))
            return reason
        except Exception:
            pass
    elif selector == _PANIC_SELECTOR:
        try:
            return f"panic 0x{int(payload, 16):02x}"
        except Exception:
            pass
    return f"reverted (selector 0x{selector})"


def _run_simulation(EVM, chain, from_addr, to_addr, data, value, block):
    """Fork, set the block env, run the call, and return its logs. Any
    rate-limit / revert surfaces as an exception to ``simulate_logs``."""
    if block is not None:
        evm = EVM(fork_url=chain.rpc_url, fork_block=hex(block["number"]))
        _apply_block_env(evm, block)
        log.debug("simulating at fork block %s (ts=%s)",
                  block["number"], block["timestamp"])
    else:
        evm = EVM(fork_url=chain.rpc_url)
    calldata = b""
    if data and data not in ("0x", "0X"):
        calldata = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    kwargs = {
        "caller": to_checksum_address(from_addr),
        "to": to_checksum_address(to_addr),
        "calldata": calldata,
    }
    if value:
        kwargs["value"] = int(value)
    evm.message_call(**kwargs)
    out = []
    for lg in evm.result.logs:
        # pyrevm Log: .address (str), .topics (list[str]), .data is a
        # (topics, data_bytes) tuple — the payload is [1].
        out.append({
            "address": lg.address,
            "topics": list(lg.topics),
            "data": "0x" + lg.data[1].hex(),
        })
    return out


def simulate_logs(chain, from_addr: str, to_addr, data, value,
                  *, evm_cls=None, retries=4, sleep=time.sleep):
    """Simulate the tx against ``chain``'s forked latest state and return
    its event logs as ``decode_event``-ready dicts::

        [{"address": "0x…", "topics": ["0x…", …], "data": "0x…"}, …]

    Returns ``None`` when pyrevm is unavailable, the tx is a contract
    creation (no ``to``), or the simulation reverts/errors — the caller
    then shows no preview rather than a wrong one; the revert reason and
    fork block are logged.

    Forking issues one RPC request per cold storage slot, so on a
    throttled endpoint the burst can hit a rate-limit even though a lone
    simulation succeeds. Those are retried with exponential backoff
    (``retries`` attempts) to catch a quieter window; a genuine revert is
    *not* retried. ``evm_cls`` is an injection seam for tests; when set we
    skip the (networked) block-env fetch so tests stay hermetic. ``sleep``
    is injectable so retry tests don't actually wait."""
    if not to_addr:
        return None   # contract creation — not previewed
    injected = evm_cls is not None
    EVM = evm_cls
    if EVM is None:
        try:
            from pyrevm import EVM
        except Exception:
            return None
    fork_no = None
    for attempt in range(retries):
        try:
            # Real block context (production only); injected tests fork-free.
            block = None if injected else _latest_block(chain)
            fork_no = block["number"] if block else None
            return _run_simulation(
                EVM, chain, from_addr, to_addr, data, value, block)
        except Exception as e:
            if _is_rate_limited(e) and attempt < retries - 1:
                delay = min(0.75 * (2 ** attempt), 4.0)
                log.info("simulation rate-limited by RPC; retry %d/%d in "
                         "%.1fs", attempt + 1, retries - 1, delay)
                sleep(delay)
                continue
            log.warning("simulation failed (fork block %s): %s",
                        fork_no, _decode_revert(str(e)))
            return None
