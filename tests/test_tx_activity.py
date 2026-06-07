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
