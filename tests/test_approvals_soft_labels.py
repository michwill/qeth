"""Soft (self-reported) spender names for approvals: the spender's own ERC-20
name or its verified ABI contract name (proxy-resolved), shown in ITALIC to flag
lower confidence than a definitive OLI name-tag. Covers the data resolution
(worker + Blockscout helper) and the italic UI rendering."""

import json
from types import SimpleNamespace

import qeth.plugins.approvals as ap
from qeth.plugins.approvals import ApprovalsPanel, ScanWorker
from qeth.plugins.approvals.cache import ApprovalsCache
from qeth.plugins.approvals.discovery import ApprovalRow
from qeth.transactions import (
    fetch_contract_display_name, meaningful_contract_name,
)

CHAIN = SimpleNamespace(chain_id=1, name="Ethereum", symbol="ETH")
OWNER = "0x" + "a1" * 20
TOKEN = "0x" + "cc" * 20
TOKEN_SPENDER = "0x" + "2d" * 20     # a spender that is itself an ERC-20
PROXY_SPENDER = "0x" + "3e" * 20     # a verified BeaconProxy → VToken
BARE_SPENDER = "0x" + "ee" * 20      # neither a token nor verified → address


# --- meaningful_contract_name: proxy resolution + shell skip ------------------

def test_meaningful_name_prefers_non_proxy_implementation():
    assert meaningful_contract_name(
        "BeaconProxy", [{"name": "VToken"}]) == "VToken"


def test_meaningful_name_skips_bare_proxy_shell():
    assert meaningful_contract_name("TransparentUpgradeableProxy", []) == ""
    assert meaningful_contract_name("ERC1967Proxy", None) == ""


def test_meaningful_name_uses_own_name_when_not_a_proxy():
    assert meaningful_contract_name("UniversalRouter", []) == "UniversalRouter"


def test_meaningful_name_skips_proxy_impl_too():
    # both proxy and its "impl" are proxy shells → nothing meaningful
    assert meaningful_contract_name("BeaconProxy", [{"name": "BeaconProxy"}]) == ""


# --- fetch_contract_display_name (keyless Blockscout v2, fake transport) ------

def _v2(payload):
    return lambda url, timeout: json.dumps(payload).encode()


def test_fetch_name_resolves_proxy_to_implementation():
    tr = _v2({"is_verified": True, "name": "BeaconProxy",
              "implementations": [{"name": "VToken"}]})
    assert fetch_contract_display_name(1, PROXY_SPENDER, transport=tr) == "VToken"


def test_fetch_name_unverified_is_empty():
    tr = _v2({"is_verified": False, "name": "Whatever"})
    assert fetch_contract_display_name(1, BARE_SPENDER, transport=tr) == ""


def test_fetch_name_404_body_is_empty():
    tr = _v2({"message": "Not found"})        # no is_verified flag
    assert fetch_contract_display_name(1, BARE_SPENDER, transport=tr) == ""


def test_fetch_name_unsupported_chain_no_network():
    calls = []
    tr = lambda url, timeout: calls.append(url) or b"{}"
    assert fetch_contract_display_name(99999, BARE_SPENDER, transport=tr) == ""
    assert calls == []                         # no Blockscout instance → never fetched


# --- ScanWorker._fetch_soft_labels: ERC-20 name, residual, budget, memo -------

class _Client:
    def __init__(self, erc20=None):
        self._erc20 = erc20 or {}

    def multicall_erc20_metadata(self, tokens, **k):
        return {t.lower(): self._erc20[t.lower()]
                for t in tokens if t.lower() in self._erc20}


def _worker(erc20=None):
    return ScanWorker(CHAIN, OWNER, object(), object(), [],
                      SimpleNamespace(missing=lambda c, t: [], get=lambda c, t: {},
                                      put_many=lambda c, i: None),
                      client_factory=lambda c: _Client(erc20))


def test_soft_label_from_own_erc20_name():
    w = _worker(erc20={TOKEN_SPENDER: {"symbol": "vcrvUSD_Curve",
                                       "name": "Venus crvUSD (Curve)"}})
    out = w._fetch_soft_labels(w._client_factory(CHAIN), 1, [TOKEN_SPENDER])
    assert out[TOKEN_SPENDER] == "Venus crvUSD (Curve)"


def test_soft_label_erc20_symbol_when_no_name():
    w = _worker(erc20={TOKEN_SPENDER: {"symbol": "VTKN", "name": ""}})
    out = w._fetch_soft_labels(w._client_factory(CHAIN), 1, [TOKEN_SPENDER])
    assert out[TOKEN_SPENDER] == "VTKN"


def test_soft_label_residual_falls_back_to_contract_name(monkeypatch):
    monkeypatch.setattr(ap, "fetch_contract_display_name",
                        lambda cid, addr, **k: "VToken" if addr == PROXY_SPENDER else "")
    w = _worker()                                   # no ERC-20 metadata for anyone
    out = w._fetch_soft_labels(w._client_factory(CHAIN), 1,
                               [PROXY_SPENDER, BARE_SPENDER])
    assert out[PROXY_SPENDER] == "VToken"           # verified contract name
    assert out[BARE_SPENDER] == ""                  # nothing → bare address


def test_soft_label_memoized_across_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_contract_display_name",
                        lambda cid, addr, **k: calls.append(addr) or "X")
    w = _worker()
    w._fetch_soft_labels(w._client_factory(CHAIN), 1, [BARE_SPENDER])
    w._fetch_soft_labels(w._client_factory(CHAIN), 1, [BARE_SPENDER])
    assert calls == [BARE_SPENDER]                  # second call served from memo


def test_soft_label_contract_name_lookups_are_budgeted(monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "fetch_contract_display_name",
                        lambda cid, addr, **k: calls.append(addr) or "n")
    w = _worker()
    w._softname_budget = 2
    spenders = ["0x" + f"{i:02x}" * 20 for i in range(5)]
    out = w._fetch_soft_labels(w._client_factory(CHAIN), 1, spenders)
    assert len(calls) == 2                           # capped
    assert sum(1 for s in spenders if out[s]) == 2   # only the funded two named


# --- UI: italic rendering + reveal --------------------------------------------

def _panel(qtbot):
    p = ApprovalsPanel(host=None)
    qtbot.addWidget(p)
    return p


def _soft_row(spender=TOKEN_SPENDER):
    return ApprovalRow(token=TOKEN, spender=spender, allowance=1, symbol="USDC",
                       decimals=6, spender_soft_label="Venus crvUSD (Curve)")


def test_soft_label_shown_italic(qtbot):
    p = _panel(qtbot)
    p.append_rows([_soft_row()])
    leaf = p.tree.topLevelItem(0).child(0)
    assert leaf.text(0) == "Venus crvUSD (Curve)"    # the self-reported name
    assert leaf.font(0).italic() is True             # cursive → lower confidence
    assert "self-reported" in leaf.toolTip(0).lower()
    assert TOKEN_SPENDER in leaf.toolTip(0)


def test_hard_name_tag_not_italic(qtbot):
    p = _panel(qtbot)
    p.append_rows([ApprovalRow(token=TOKEN, spender=TOKEN_SPENDER, allowance=1,
                               symbol="USDC", decimals=6,
                               spender_label="Uniswap: Router",
                               spender_soft_label="ignored when a tag exists")])
    leaf = p.tree.topLevelItem(0).child(0)
    assert leaf.text(0) == "Uniswap: Router"
    assert leaf.font(0).italic() is False            # definitive → regular weight


def test_reveal_shows_address_regular_weight(qtbot):
    p = _panel(qtbot)
    p.append_rows([_soft_row()])
    leaf = p.tree.topLevelItem(0).child(0)
    p._hovered = leaf
    p._refresh_reveal()
    assert leaf.text(0) == TOKEN_SPENDER             # address on hover
    assert leaf.font(0).italic() is False            # address is never cursive
    # un-hover restores the italic soft name
    p._hovered = None
    p._refresh_reveal()
    assert leaf.text(0) == "Venus crvUSD (Curve)"
    assert leaf.font(0).italic() is True


def test_bare_address_row_not_italic(qtbot):
    p = _panel(qtbot)
    p.append_rows([ApprovalRow(token=TOKEN, spender=BARE_SPENDER, allowance=1,
                               symbol="USDC", decimals=6)])   # no labels at all
    leaf = p.tree.topLevelItem(0).child(0)
    assert leaf.text(0) == BARE_SPENDER
    assert leaf.font(0).italic() is False


# --- cache round-trips the soft label -----------------------------------------

def test_cache_persists_spender_soft_label(tmp_path):
    c = ApprovalsCache(tmp_path)
    row = ApprovalRow(token=TOKEN, spender=TOKEN_SPENDER, allowance=1,
                      symbol="USDC", decimals=6,
                      spender_soft_label="Venus crvUSD (Curve)")
    c.save(1, OWNER, [row], last_block=10)
    rows, _ = c.load(1, OWNER)
    assert rows[0].spender_soft_label == "Venus crvUSD (Curve)"
