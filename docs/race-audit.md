# Race-condition audit & fix plan (July 2026)

A deep audit of the whole codebase for race conditions, prompted by the
June/July wack-a-mole series in tokens (b204e6b, e9d37f2, c6d4b8f, 2eb38e9)
and ENS (98fe855, 530fca3, 75622dd, 5935689). Five parallel reviews covered:
the token pipeline, ENS, UI worker lifecycle, the network/device layer, and
persistence. High-impact findings were re-verified by hand against the code.

Line numbers reference the tree at commit e6f9d07 (branch point of
`race-fixes`). They will drift as fixes land — search for the named
symbols.

**Status legend:** CONFIRMED = the interleaving was traced end-to-end in
code (several were additionally hand-verified). PLAUSIBLE = the mechanism
is real but the trigger needs conditions that were not reproduced.

## Progress (branch `race-fixes`)

Done and committed:
- **1a/1b** — both nonce collisions (re-resolve at sign time; cache-scan
  floor, ws-independent, counts confirmed).
- **1c/1d** — confirmation dedupe (`_confirmed_seen` + snapshot rebuild) and
  drop-reading wall-clock spacing.
- **1e/1f** — `Dialog` blocks reject/close mid-sign; `eth_accounts` single
  read; signing-request setup rejects the bridge future on failure. (Finding
  6 intentionally skipped — can't-happen.)
- **2a/2b** — `chain.head_balances` co-reads native (`getEthBalance`) and a
  per-chunk block. (P2 step 0 / ledger prerequisite.) **Superseded by
  per-token block-stamping** (see below): it now returns each token's own
  chunk height in a `blocks` map; the conservative min is kept only for native
  + the reconcile wait.
- **4b** — `Store.save` copies `accounts` under the lock + seq-ordered writes.
- **3a/3g** — ENS per-generation `_epoch` drops stale discovery/verify
  landings.
- **P3 ENS block-stamp** — records and ownership reads now co-read their
  height and order freshness by block, replacing the verified-ratchet /
  lagging-proof / value-agreement guards with one reducer each. Closes **3b**
  (forced re-read keeps its cache anchor — the stale pre-write worker is
  block-ordered out, not left unguarded), **3c** (the value-agreement worker
  escape is gone), **3e** (a changed record's fast read at a newer block
  replaces even a cached verified value — so a Helios-less session updates),
  **3d** (ownership catchup is a block-wait, immune to one name's value
  vetoing the whole verified pass), **3g-b** (`populate()` skips an identical
  tree and restores fold + selection), and **3f** falls out of the epoch (the
  catchup flag is set with the epoch, so only the latest refresh's discovery —
  carrying the current flag — survives). Records cache schema gains a block
  (default 0). Also **3h**: the `_start` fallback tracks its QThread.
- **5d** — stale gas estimates dropped by emitting-worker identity.
- **5e** — helios `_stop_all` snapshots under the lock; `ledger_hid.submit`
  enqueues under the lock; `_ensure_heavy_imports` / `_ensure_async_imports`
  publish their guard symbol last.
- **P2 BalanceLedger** — `qeth/balance_ledger.py` is now the single owner of
  the freshness stamps + ordered cache mutation (was two dicts + duplicated
  logic). Every balance write funnels through it: `apply_read` (absolute,
  block-ordered; a block-less read is the weakest — never overrides an ordered
  value or drops on zero), `apply_native` (ordered ws-poll native, **2d**),
  `apply_floor` (idempotent receipt credit — **finding 5**); discovery's native
  is ordered too (**2c**). `reset_chain` on ws reconnect is the reorg escape
  (**step 3** — floors can't over-stamp post-min-block, so this only covers the
  blind-gap). Discovery's persist keeps hidden held tokens (**step 2**
  cache-invariant). Token sources read through getters (post-construction swap).
  Unit + wiring coverage in `tests/test_balance_ledger.py` /
  `tests/test_live_wiring.py`; verified on the real fork.

Fixes surfaced while verifying (not in the original audit):
- **aiohttp pycares segfault** — `--system-site-packages` gives aiohttp the
  c-ares `AsyncResolver`, whose `pycares._run_safe_shutdown_loop` thread
  segfaults when it overlaps the Qt event loop at teardown (latent 0/8,
  flipped to ~87% by the 2a/2b timing shift). Forced `ThreadedResolver` in
  `__main__` + conftest → 0/6.
- **worker-signal lambdas** — the 5d/3a generation guards first bound their
  generation into a lambda connected to a worker signal; a lambda isn't
  receiver-tracked, so a worker outliving a closed dialog / torn-down plugin
  fires into a deleted object. Reworked to bound-method connections +
  `self.sender()` identity/epoch.

Deferred / not yet done:
- **P2 remaining satellites** — the three genuinely low-value/high-churn ones:
  - `_unpriced_since` account keying — one account's expired grace can hide
    another's just-received token (LOW display glitch); the grace map is shared
    with the panel's display-time filter, so keying it by account is medium
    churn across both.
  - `_carry_forward_absent` shouldn't stamp a carried (not-actually-read) value
    at the aggregate block (a later correct read at a lower block is discarded);
    rare, needs marking carried entries through the merge.
  - `_reconcile_up_to_block` singleShot chains — an exit-only QThread-abort risk
    that mostly can't fire (the event loop is gone by the time the plugin is
    destroyed); would need a plugin shutdown hook.
- **P3 remaining 3h (LOW)** — `_force_reread` is a plugin-wide flag a
  concurrent non-forced records worker can clear before the forced worker's
  fast pass (so a setAddr-to-zero might not clear the row until the next
  refresh); carrying forced-ness per-worker is clean but breaks the direct-call
  tests (needs `sender()`), so deferred. And a verify worker whose FAST read
  failed still emits the verified read unconditioned (block-wait's `not fast`
  path) — could drop a just-acquired name on a lagging proof; the safer policy
  (emit vs keep-unverified) is ambiguous.
- **4a single-instance lock** — UX call (forbid vs focus-raise) for the user.
- **P5** — being picked off individually.

Post-fix re-review notes (adversarial pass over the branch):
- **min-block trade — REVISED, the min was wrong too.** The "self-healing"
  claim for (ii) was false in practice: on a large multi-chunk read behind a
  load balancer (Arbitrum), one *persistently*-lagging backend keeps the batch
  min below a token's freshness floor, so that token's authoritative zero is
  rejected as stale on *every* sweep — a spent token stuck showing its old
  balance until a restart (a real user report). A single per-batch block is
  wrong both ways (min under-claims → stuck; max/first over-claims →
  resurrect). Fixed by **per-token block-stamping**: `head_balances` returns
  each token's own chunk height (it already co-read `getBlockNumber` per
  chunk), and value+block are co-located per chunk, so a lagging chunk's stale
  value carries its own lagging block and is correctly rejected — closing 2a
  *and* the stuck case. The min survives only for native ordering (protected by
  the ws poll's own-block read) and the reconcile catch-up wait.
- **native fallback mis-stamp** (BalanceWorker, rare chunk-failure path):
  the fallback `get_balance` isn't co-read with the surviving chunks' block,
  so the native stamp can be skewed — transient, self-healing, narrower than
  the pre-change every-refresh exposure. Comment fixed to say so.
- **remaining closure-connections to worker signals** (same class as the
  fixed 5d/3a lambdas, pre-existing): `_make_identity_row`'s nested `_apply`
  (plugins/transactions.py) and the BalanceWorker lambdas in
  plugins/tokens.py (~:584, :715, :1334) — receivers are the app-lifetime
  plugin or MainWindow-parented dialogs, so exposure is app-shutdown only.
  Sweep alongside 5g.
- **non-multicall custom chains**: the tokenless refresh now emits
  block=None (was a real `get_block_number`), so native ordering is
  inactive there — single-RPC chains, no LB skew, LOW; folds into the
  open block=None-weakest-read satellite.
- **410c12b commit message**: its "tidies Dialog.closeEvent" line describes
  a no-op (the tweak cancelled itself out pre-commit); code is correct.

## The unifying diagnosis

Every guard the recent series added approximates one invariant:

> Per (chain, account, key), displayed/cached state is **a value at a block
> height**; a write applies iff its height ≥ the recorded one.

But it's enforced by six hand-rolled mechanisms in tokens and five in ENS,
and the writers added *after* the mechanism (receipt delta credit, ws native
poll, discovery's native leg) skipped it entirely. The fix plan is therefore
two refactors that make the invariant structural (`BalanceLedger` for
tokens, block-stamp + epoch for ENS), plus point fixes for races outside
those subsystems — the worst of which are two nonce races.

---

## Priority 1 — funds-affecting point fixes (small, independent commits)

### 1a. Same-nonce collision between stacked composer dialogs — CONFIRMED, hand-verified

- The nonce is computed once, in `GasSuggestionWorker.run()` at dialog-open
  (`plugins/transactions.py:4235-4241`, `max(mined, nonce_floor)`), stored in
  `_suggested_nonce`, and read back verbatim by `finalised_request()` at
  Confirm (`:4939`) — potentially minutes later. `_begin_sign`
  (`ui.py:931-943`) does not re-check.
- `nonce_floor` is sampled once at `_kick_gas` time (`:4820`).
  `SignTransactionDialog` kicks gas once in `__init__`; `SendTokenDialog`
  re-kicks only on recipient change.
- Composer dialogs are non-exec and stack: each dapp `eth_sendTransaction`
  opens its own dialog (`rpc.py` → `SignerBridge` → `ui.py:678`), the GUI
  Send is another. Nothing serializes or coalesces them (unlike
  `wallet_addEthereumChain`, which coalesces via `_pending_chain_add`).
- Interleaving: dialogs A and B open for the same account; both capture
  nonce N. Confirm A → broadcast. Confirm B → signs nonce N → **B replaces A
  in the mempool** (or bounces "replacement underpriced" after the user
  already confirmed on the Ledger). A's row later flips to "Dropped" with no
  explanation.

**Fix:** re-resolve the nonce at signing time — in `finalised_request()`
(and/or on `sign_requested`), recompute
`max(self._suggested_nonce, pending_nonce_floor(chain, from))` immediately
before building the SigningRequest. Optionally also bump open dialogs'
nonces on `add_pending` for the same account.

### 1b. `pending_nonce_floor` is dead when `QETH_LIVE_WS=0` — CONFIRMED, hand-verified

`pending_nonce_floor` (`plugins/transactions.py:1135-1147`) reads
`_live_pending_provider` → `_live_snapshot`; `_rebuild_live_snapshot`
(`:1175-1186`) early-returns when `self._live_watcher is None`. With the ws
watcher disabled the snapshot stays `{}` forever → the floor is always
`None` → a back-to-back send (or a dapp approve-then-swap) re-reads
`get_transaction_count(.., "latest")`, which doesn't include the first tx
yet → same nonce → the second tx replaces the first. The floor's own
comment (`:4183-4185`) says it exists precisely to prevent this.

**Fix:** make `pending_nonce_floor` scan `self._cache` directly (all callers
are main-thread; `_build_pending_snapshot` is pure and cheap), or maintain
the snapshot unconditionally regardless of the watcher.

### 1c. Confirmation multi-fire × non-idempotent delta credit — CONFIRMED, hand-verified

Three stacking defects:

- `note_receipt_logs` is forwarded **before** the `if not t.pending: return`
  dedup (`plugins/transactions.py:1407` vs `:1416`), deliberately (per the
  comment) — but that makes it run on every duplicate delivery.
- Nothing rebuilds `_live_snapshot` on confirm — only the 10 s poller tick
  (`:367`) and `add_pending` (`:1379`). So on a 2 s chain the ws watcher
  keeps re-probing the already-confirmed tx every head
  (`live_watcher.py:435-465`) and re-emits `confirmed` 2–5× until the next
  poll tick. The polling `PendingProbeWorker` can add one more.
- The receipt credit is a raw delta with no ordering:
  `tok.balance_raw = max(0, int(tok.balance_raw) + int(delta))`
  (`plugins/tokens.py:1224`), never consulting `_balance_block`.

User-visible: a received token's balance doubles/triples on screen for
~10 s (the authoritative `_reconcile_up_to_block` read heals it, so it
*oscillates*). Even a single confirm double-counts when the ws absolute
read applied first (order-dependent, ~half of own-wallet receives). Each
duplicate also kicks a full discovery + ENS re-read.

**Fix (all three, cheap):**
1. `_rebuild_live_snapshot()` at the top of `_on_receipt_confirmed` /
   `_on_tx_dropped`.
2. Dedupe forwards per (chain_id, tx_hash) (`receipt["transactionHash"]`).
3. Make the credit idempotent: skip when
   `_balance_block[(cid, acct, token)] >= receipt_block` — an authoritative
   read at/after the receipt block already includes it. (Subsumed by
   `BalanceLedger.apply_floor` in Priority 2; do the cheap check now.)

Also fix the stale docstring on `_apply_receipt_credit_to_cache` ("Saves the
cache on the worker thread" — it runs on the GUI thread).

### 1d. False permanent "Dropped" from per-block ws drop readings — CONFIRMED mechanism

`DROP_CONFIRM_READINGS = 3` (`plugins/transactions.py:935-940`) was
calibrated against 10 s poll ticks ("~30 s for a receipt to propagate
through an LB"), but the ws watcher emits `dropped` per block
(`live_watcher.py:466-470`) — three readings in ~6 s on Base/OP, plus the
poller can add a fourth. A mined-but-receipt-lagging tx behind a
load-balanced RPC gets flipped to terminal Dropped: ⊘ row, `raw_signed`
discarded, rebroadcast stops — and the flip is **permanent**, because the
later `confirmed` bails at `if not t.pending` (`:1416`).

**Fix:** rate-limit counted drop readings per hash — require a minimum
wall-clock spacing (≥ the poll interval) between readings, so "3
consecutive readings" means what it was calibrated to mean.

### 1e. Sign dialog dismissable mid-signing → dapp told "cancelled", tx lands anyway — PLAUSIBLE

`set_signing_in_progress(True)` disables the Cancel *button* only
(`plugins/transactions.py:4894-4912`); `QDialog.reject()` via Esc or WM
close is not gated (`ui.py:750` connects `rejected` → bridge reject; no
`reject()`/`closeEvent` override exists in `_TxComposerDialog`/`Dialog`).
Ledger signing continues in the worker; the broadcast succeeds; the future
double-resolution itself is guarded (`fut.done()`), so the only damage is
the dapp being told "User cancelled" for a tx that lands on-chain.

**Fix:** track a `_signing` flag in `_TxComposerDialog`; override `reject()`
and `closeEvent` to ignore (or confirm) while a sign worker is in flight.

### 1f. rpc.py signing-bridge hygiene — CONFIRMED / PLAUSIBLE

- `eth_accounts` builds `[self.store.default_account] if
  self.store.default_account else []` (`rpc.py:591`) — two unlocked reads on
  the asyncio thread; `remove_account` on the GUI thread can null the
  attribute between them → a dapp receives `[null]`. Read once into a local.
- Any exception in `MainWindow._on_signing_request` (`ui.py:678-734`) —
  e.g. the dialog ctor raising — leaves the bridge future unresolved
  forever; the dapp request hangs, and (see 5a) so does its whole WS socket.
  Wrap the slot body in try/except → `signer_bridge.reject(fut, …)`.
- `SignerBridge.resolve/reject` after the RPC loop closed raises
  `RuntimeError: Event loop is closed` inside a Qt slot (shutdown with a
  dialog open). try/except in `resolve`/`reject`.

---

## Priority 2 — tokens refactor: `BalanceLedger`

### Why more point patches won't hold

Write paths today (complete enumeration):

| # | Path | Semantics | Ordering today |
|---|------|-----------|----------------|
| 1 | `_persist_targeted_balances` (ws values / `_on_reconcile_read` / `_reconcile_displayed_balances` / eager is_new_view read) | absolute per-token; zero drops row | per-token `_balance_block`; native per-account `_last_applied_block` |
| 2 | `_on_combined_ready` merge + `_save_wallet_cache` | absolute merge, then **full cache replace** filtered to the visible set | tokens ordered; **native unordered** (2c); replace drops hidden/dust/grace-expired from disk — contradicts `_filter_hidden_from_cache`'s invariant |
| 3 | `_apply_receipt_credit_to_cache` | **delta**, recipient side | none (1c) |
| 4 | `_record_nonzero_block` | floor bump, no value | monotonic max — the only idempotent writer |
| 5 | ws native poll → `on_native_balance` → `_on_balance_refresh` + `_touch_cached_native` | absolute native | none (2d) |
| 6 | price appliers | price-only | n/a |

Guards accumulated: `_balance_block`, `_last_applied_block`,
`_record_nonzero_block`, `read_failed`, `_carry_forward_absent`,
`nothing_changed`, `_is_current_view` vs `_displayed_view`,
`_pending_rerender`, `_discovery_in_flight`, the min-block retry, the
unpriced grace. (`_recently_zeroed` is already gone — e9d37f2 replaced it.)

### 2a. Multicall chunking breaks the atomic-stamp premise — HIGH, CONFIRMED, hand-verified

`head_balances` queues `mc.block_number()` **once** (`chain.py:419`), but
`Multicall._flush` issues one independent `eth_call` per 100-slot chunk
(`chain.py:566`). Behind DRPC's LB each chunk can land on a different
backend at a different height, yet `_on_combined_ready` stamps **every**
token with chunk 1's block (`plugins/tokens.py:1633-1639`). Discovery sets
on majors are "a few hundred per chain" → always multi-chunk.

Interleaving: USDT (sorts into chunk 2) fully sent at block B; ws applied
the correct 0 at B. Sweep discovery: chunk 1 fresh backend → block B+1;
chunk 2 lagging backend at B−1 → pre-send balance. Stamped B+1 ≥ B → the
stale balance **overwrites the zero, resurrects the row, and raises the
floor** — the exact e9d37f2 bug, reintroduced through the batching seam.

### 2b. `(native, block)` pair is not co-read — MED, CONFIRMED

`BalanceWorker` reads native via a separate `eth_getBalance` HTTP request
from the multicall that produces the stamp (`plugins/tokens.py:215-234`);
the apply gate is `block < _last_applied_block` — **equal applies**
(`:869-881`). A lagging native read stamped with a fresh multicall block
regresses a just-updated native (or inflates the floor and blocks the next
correct read).

### 2c. Discovery's native write bypasses ordering entirely — HIGH, CONFIRMED

`pv["native_wei"]` is captured at `on_balances` time
(`plugins/tokens.py:1496-1499`), rendered unordered (`:1605,1663-1674`),
and persisted by `_save_wallet_cache` (`:1682,1845-1877`) which never
consults `_last_applied_block`. The prices/risk legs add seconds between
read and apply — exactly the window a confirm lands in. A sweep that read
native pre-send persists the pre-send value *after* the ws applied the
post-send one; on the ws-throttled 300 s sweep the stale value can sit for
up to a minute. Token values got block-ordering in e9d37f2; native did not.

### 2d. ws native poll is unordered → regression + duplicate "received ETH" notification — MED, CONFIRMED

`_emit_native` reads at `"latest"` with no block
(`live_watcher.py:418-433`); `on_native_balance` applies without consulting
`_last_applied_block` (`plugins/tokens.py:934-945`). 2eb38e9's own commit
message notes any connection, ws included, can jump backwards behind an LB.
A backwards jump regresses the shown native AND resets `_last_native_seen`;
the next poll reads the correct balance → `_notify_native_delta`
(`:947-962`) fires a duplicate desktop notification for money that arrived
minutes ago.

### The refactor

**Step 0 (prerequisite, `chain.py`) — fix the reads or the ordering is
garbage-in:**
- Queue `getBlockNumber()` in **every** chunk of `Multicall._flush`; return
  per-chunk (or per-token) blocks, or stamp all tokens with the **minimum**
  chunk block (conservative: may under-claim freshness, never over-claims).
- Read native inside the same aggregate: Multicall3 has
  `getEthBalance(address)` (selector `0x4d2301cc`) — add
  `mc.eth_balance(holder)` alongside `mc.block_number()`.
- This step alone fixes 2a and 2b and is worth a standalone commit.

**Step 1 — `BalanceLedger`** (~150 lines, main-thread-only, sole owner of
the wallet cache and ONE ordering map keyed `(chain_id, account, asset)`,
where asset `""` = native — retiring `_last_applied_block`):

```python
class BalanceLedger(QObject):
    def apply_read(self, chain_id, account,
                   reads: dict[str, tuple[int, int]],   # asset -> (value, block)
                   *, zero_is_authoritative: bool = True) -> bool:
        """block REQUIRED (co-read with the value). Applies each asset iff
        block >= floor; authoritative zero drops the row; returns changed."""
    def apply_floor(self, chain_id, account, asset, block,
                    min_value: int = 1) -> None:
        """Receipt-side: 'balance is known >= min_value as of block'.
        Idempotent — replaces the delta credit and _record_nonzero_block."""
    def apply_prices(self, chain_id, account, prices) -> bool: ...
    changed = Signal(object, str)   # (chain_id, account) -> ONE render path
```

- Path 1 is already `apply_read` — mechanical move.
- Path 2 collapses: discovery stops replacing the cache and just calls
  `apply_read` + `apply_prices`. `_carry_forward_absent` becomes unnecessary
  (absence simply isn't in `reads`); `read_failed` becomes "don't call".
- Path 3 becomes `apply_floor(receipt_block, min_value=cached+Δ)` —
  duplicate confirms and credit-after-absolute become harmless by
  construction.
- Path 5 routes through `apply_read` once `_emit_native` co-reads
  `eth_blockNumber` on its socket (same-socket co-read is much tighter than
  http, though still not one request — acceptable).
- The `changed` signal centralizes `_is_current_view` / `_pending_rerender`
  / re-render — one render decision instead of five call sites.
- Stays OUT of the ledger (read-side policy, correctly separate): the
  `min_block` retry loop, `_discovery_in_flight`, debounce timers, and the
  display filters (hidden/dust/unpriced-grace).

**Step 2 — cache-invariant change.** Merge-only persistence means the disk
cache keeps dust/unpriced/hidden entries; visibility becomes purely
display-time (`_compute_visible_tokens` already mostly is). Add an eviction
rule (drop authoritative-zero entries; cap N). This also resolves the
existing writer disagreement where discovery's replace-save silently
deletes user-hidden tokens that `_filter_hidden_from_cache` documents as
deliberately kept.

**Step 3 — reorg escape, in one place.** Monotonic floors freeze state if a
floor was stamped too high or a reorg rewinds the chain. Age out floors
older than ~2 minutes, or reset a chain's floors on ws `link_state`
reconnect. Today this weakness is smeared invisibly across three maps.

**Step 4 — tests.** `test_live_anvil.py` / `test_live_wiring.py` assert on
the current persist functions and the filtered save — port them, and add a
>100-token discovery case to lock in the per-chunk stamp fix.

### Priority-2 satellites (independent small fixes)

- `_unpriced_since` is keyed `(chain_id, contract)` without the account
  (`plugins/tokens.py:354`) — one account's expired grace instantly hides
  another account's just-received token. Key by (chain, account, contract).
- `block=None` reads bypass every guard — including the authoritative-zero
  drop (`:886-889`, `:1636-1641`) and the `min_block` retry (`:726-731`).
  Reachable when chunk 1 (carrying `getBlockNumber`) fails while later
  chunks succeed. A block-less read must be the *weakest*: apply only to
  tokens with no recorded floor, never drop on zero, never satisfy
  `min_block`. (Moot for paths moved onto the ledger, which requires a
  block.)
- `_carry_forward_absent` stamps un-read (carried) values at the
  aggregate's block (`:1500-1505` + `:1633-1639`) — don't bump floors for
  carried entries. (Dissolves under the ledger.)
- `_reconcile_up_to_block` singleShot chains (20 × 700 ms) outlive account
  switches (wasted RPC only — writes are keyed and ordered) and **app
  shutdown**: a retry firing during teardown can start a QThread whose
  Python ref dies while running → the classic QThread-destructor abort on
  exit. Add a `_shutting_down` flag checked in the retry, and stop
  `_refresh_timer` + pending retries from `closeEvent`.

---

## Priority 3 — ENS refactor: block stamp + epoch

### Open races

**3a. No generation token — a stale verify worker repaints old state with a ✓ — HIGH, PLAUSIBLE (fully traced; needs two overlapping workers + Helios lag, both normal).**
`_on_verified` guards only `host.selected_address != address`
(`plugins/ens.py:1957`). Two verify workers for one address overlap
routinely (the load-time worker lives 30–60 s: 25 s sidecar wait + 3×2 s
retries; a post-write refresh spawns another). Worker V1's fast AND
verified reads can both predate the user's write → they agree with each
other → V1 emits `verified=True` for the **old** owner *after* V2's fast
pass painted the new one: old rows re-painted with a green ✓,
`_controller`/`_registrant` reverted (Set-manager wrongly re-offered — the
75622dd on-chain-revert scenario, resurrected one level up),
`_denied.discard` (`:1963`) resurrects a dropped name.

**3b. Forced re-read pops the anchor the anti-regression guards key on — MED-HIGH, CONFIRMED, hand-verified.**
`_on_records_requested(force=True)` pops `_rec_cache[nl]` and forgets the
disk entry (`plugins/ens.py:2033-2035`), but both guards in
`_on_records_ready` dereference `prev = self._rec_cache.get(nl)`
(`:2070-2082`). A still-in-flight older records worker (verified phase can
live ~8 s wait + retries) then lands its **pre-write** records unguarded
(`prev is None`) and re-anchors them — after which the *fresh* post-write
fast read is dropped by the verified-ratchet (`:2073`). Shows the old
record, verified-green, until the next rediscover.

**3c. `EnsRecordsWorker` kept the `attempt == tries-1` escape 75622dd removed from ownership — MED, CONFIRMED.**
`plugins/ens.py:342`: a catchup records worker that outlives a second write
emits the lagging value as `verified=True` on its last attempt. Normally
the plugin-side guard suppresses it — but 3b shows the guard's anchor can
be gone.

**3d. `_states_agree` couples all names; single-shot verify → wallet-wide verification blackout — MED, CONFIRMED.**
`plugins/ens.py:348-359, 389-399`: one name changing externally between the
fast and verified reads (or one name served by a lagging failover backend)
fails the batch agreement → the verified pass emits **nothing for any
name**: no ✓, no indexer-lie drops, no expiry corrections. Non-catchup mode
is single-shot and there is no periodic refresh, so the blackout persists
until the next user-driven event. (It cannot *permanently* hide an external
change — the next worker's fresh reads agree — but "next worker" is
user-event-driven, so the window is unbounded.)

**3e. Records symmetric holes — MED, CONFIRMED.**
(i) Guard `:2080` drops a verified read that *differs from the last
unverified value* — if the record changed externally after the fast paint,
the verified read showing the newer correct value is discarded as a
"lagging proof", and `_LOADED_ROLE` (`:1070-1074`) prevents re-requesting.
(ii) The verified-ratchet (`:2073`) + a disk-cached `verified=True` seed
(`:2040-2042`) means on a session where Helios is unavailable
(`verified_read_records` returns `(…, False)` forever), **a changed record
can never update in-session** despite every fast read seeing the new value.

**3f. `_verify_catchup` one-shot instance flag is consumed by whichever discovery lands next — LOW-MED, CONFIRMED** (`:1793, :1879, :1943-1944`): a
stale discovery spends the catchup budget; the write's own discovery then
verifies single-shot → no ✓ for the user's own change this pass.
Symmetrically `_on_add_custom` overwrites a pending `True`.

**3g. Discovery races + `populate()` rebuilds — MED (7a PLAUSIBLE / 7b CONFIRMED).**
(a) Two overlapping `EnsNamesWorker`s: last-lander wins and **overwrites
the disk cache with the older name set** (`:1884-1892`) — a just-added
name/subdomain vanishes. (b) `populate()` (`:798-810`) has no
unchanged-skip: every discovery landing rebuilds the whole tree —
collapsing expansions, dropping selection, resetting `_LOADED_ROLE` — and
it fires precisely after the user's own write (rediscover ops), i.e. while
they're looking at the name they just edited. Same class 5935689 fixed one
level down.

**3h. Small:** `_force_reread` is discarded by any worker's fast pass, not
the forced worker's (`:2094-2095`) — carry forced-ness in the worker emit.
The `_start` fallback (`:2597-2602`) holds only a local ref to a running
QThread (the CLAUDE.md destructor trap) — add a plugin-side tracked set.
A verify worker whose fast read failed (`{}`) emits the verified read
unconditioned (`:395`) — can falsely drop a just-acquired `owned` name.

### The refactor (each step independently shippable)

The five per-path guards are lossy proxies for "which read observed later
chain state" — a question a block number answers directly. The
infrastructure exists: `Multicall.add_block_number()` stamps a read inside
the same aggregate, and tokens already proved the pattern (e9d37f2).

1. **Per-account epoch** (~20 lines): `self._epoch += 1` in
   `_load`/`_on_refresh`; captured by every worker, checked in every
   landing slot. Kills 3a and 3g(a) immediately. (Blocks can't order across
   accounts/populates; the epoch is the generation cut address-equality
   fails to provide.)
2. **Records block-stamp**: stamp both read paths;
   `_rec_cache[nl] = (rec, block, verified)`; ONE reducer — *accept iff
   `block > shown.block`, or equal block upgrading unverified → verified*.
   A lagging proof can never regress regardless of its flag. Delete the
   `_rec_cache.pop` (3b), the worker escape (3c), the ratchet and
   lag-suppression guards (3e); catchup degenerates to "retry verified
   until its block ≥ the fast read's block" — a number compare, immune to
   external change, cheap enough to always run (dissolving 3f).
3. **Ownership block-stamp**: `OwnershipCheck.block`; `mark_verified` drops
   gated on `st.block >=` shown block; **per-name** application replaces
   `_states_agree` batch coupling (3d); catchup → block-wait.
4. **Persist blocks in the disk caches** (schema default 0 for old files):
   startup cache paints become block-ordered too — kills the Helios-less
   ratchet (3e-ii): a live fast read outranks yesterday's cached verified
   value.
5. Fold `catchup`/`force` into worker payloads (3f, 3h) — mostly dissolves
   under 2–3.
6. Optional: `populate()` becomes a merge/diff per the tokens precedent
   (3g-b), or at minimum signature-skip + expansion/selection restore.

**Keep as-is** (policy, not freshness — do not consolidate): unverified
can't drop names or paint ✓; the `registrant`-pending and
`custom`/`subnode` exemptions; look-alike ⚠ never upgraded; `ok=False`
never wipes; the render signatures `_records_sig`/`_ownership_sig` (render
idempotency protecting fold state — orthogonal and correct).

Caveat: the second multicall round (resolver `addr` reads) may execute at a
different block than round 1 — stamp each round, or use round 1's stamp as
the batch floor (under-claims freshness; never over-claims).

---

## Priority 4 — persistence & cross-process

Ground truth: **every disk write already goes through `fsatomic`**
(mkstemp + fsync + `os.replace` + dir fsync) — torn-file corruption is
solved codebase-wide, readers never see partial files. What's missing:

### 4a. No cross-process coordination — HIGH (multi-instance), CONFIRMED

No flock/QLockFile/single-instance guard anywhere. Two running instances
lose each other's mutations **permanently** via load-once +
whole-state-save:
- `config.json` (`store.py:117-205`): an account added in A is silently
  dropped by any later save from B (and was never in B's memory — nothing
  heals it). Same for custom tokens, hidden/shown overrides.
- Wallet cache: B's receipt-credited token is erased by A's later
  discovery save; A's in-process block-ordering maps can't see B.
- Tx cache: a pending tx (incl. `raw_signed` rebroadcast bytes) recorded by
  A drops off disk when B's page refresh saves.

**Fix:** single-instance guard — `QLockFile` on `~/.qeth/lock` at startup
(with a "already running" message). One change collapses the whole class.
(Alternative if multi-instance must work: flock + reload-merge around every
load-modify-save — much more work, not recommended.)

### 4b. `Store.save()` — snapshot aliasing + write outside the lock — CONFIRMED, hand-verified

`store.py:176-205`: the snapshot is built under `self._lock`, but
`data["accounts"] = self.accounts` stores a **reference** (everything else
is copied), and `json.dumps` + `atomic_write_text` run **after** the lock is
released. `Store` is genuinely cross-thread: `add_chain` is called from the
aiohttp RPC thread (`rpc.py:665`).
- A GUI `set_label` inserting a key into an account dict mid-dump →
  `RuntimeError: dictionary changed size during iteration` in the RPC
  thread's save (dapp gets a 500) or a torn snapshot.
- Two threads saving: older snapshot written last → disk regresses (memory
  stays right; healed on next save; lost on crash).

**Fix:** copy accounts under the lock
(`[dict(a) for a in self.accounts]`), and serialize writes — take a
monotonic sequence number inside the lock, write under a second IO lock,
skip if a higher sequence already wrote.

Also: `Store.load`'s parse-error path (`store.py:122-123`) returns a
default store that the next save uses to **overwrite** a merely-unparseable
config — rename the bad file to `config.json.corrupt` instead of silently
resetting.

### 4c. Two `TokenMetadataCache` instances rewrite the same file from divergent copies — LOW, CONFIRMED

`token_metadata.py:67-81`; instances in `plugins/tokens.py`
(`_token_metadata`) and `plugins/transactions.py:1041` (`_token_meta`).
Each memoizes per-chain state and `put_many` rewrites the whole file from
its own copy — last-writer-wins on the union; the other's entries vanish
from disk (cost: refetch next session). **Fix:** share one instance via the
host, or merge with the on-disk file inside `put_many`.

### 4d. `AbiCache` sentinel TOCTOU — LOW, CONFIRMED, self-healing

`abi_cache.py:144-154`: load → decide → write lets a negative sentinel
overwrite a real ABI written in between (workers genuinely concurrent:
8-wide pool + AbiFetchWorker). Window is µs; sentinel expires in 14 d.
Accept + document, or `O_EXCL`-create for sentinels only.

---

## Priority 5 — smaller / latent (batch opportunistically)

- **5a. WS head-of-line blocking** (`rpc.py:331-349`): `_ws_handler`
  processes messages strictly serially, so an unbounded signing prompt
  stalls every other request on that socket (Falkon polls every 4 s; a
  dapp pipelining a call behind `eth_sendTransaction` hangs). Same for
  HTTP batch arrays (`:315-317`). **Fix:** dispatch each message as a task;
  serialize only `ws.send_str`.
- **5b. Verified-sim floor bypass** (`rpc.py:740-747` +
  `simulate.py:485-512`): a dapp `eth_sendRawTransaction` is proxied with
  no `add_pending`, so `fork_floor_block` doesn't know about it → a
  follow-up preview forks `_VERIFIED_FORK_LAG` behind head and falsely
  reverts — the failure mode the floor exists to prevent. **Fix:** record
  proxied raw broadcasts (hash computable from the raw bytes) into the
  pending tracker.
- **5c. Wallet-tree selection hijack** (`plugins/wallets.py:1111-1138`,
  `:656-720`): an async ENS reverse-lookup resolving → `set_label` →
  `_rebuild_tree`, which ends with `setCurrentItem(default_item)` — the
  view jumps off the account the user is reading (once per resolving
  lookup); the right-slot panels clear + re-fetch. **Fix:** update the item
  text in place; if rebuilding, restore the *current* selection by address.
- **5d. Gas-suggestion has no generation guard**
  (`plugins/transactions.py:4817-4863`): edit the recipient twice → workers
  finish out of order → the stale estimate (gas for the wrong recipient)
  silently wins. **Fix:** pass the probe key through the signal and drop
  mismatches — the `_on_ens_resolved` pattern (`:5562`).
- **5e. Shutdown/lifecycle micro-fixes:**
  - `helios._stop_all` iterates `_sidecars` without `_lock` at atexit
    (`helios.py:198-203`) → RuntimeError → leaked helios processes. Snapshot
    under the lock.
  - `ledger_hid.submit` puts onto the queue outside the lock
    (`ledger_hid.py:48-56`) — can enqueue behind the shutdown sentinel →
    caller blocks 180 s (test-only today). Move the put inside; cheap
    re-entrancy insurance: run inline when already on the HID thread.
  - `RpcServer.stop()` dead window (`rpc.py:239-264`): between
    `run_until_complete` and `run_forever`, `loop.is_running()` is False →
    stop is skipped, `thread.join` burns its 5 s timeout, port stays bound.
    `call_soon_threadsafe(loop.stop)` unconditionally under try/except.
  - `_ensure_heavy_imports` publishes its guard symbol (`"Web3"`) first
    (`chain.py:52-69`) — a second thread between the assignments hits
    NameError. Assign the guard key last. Same in
    `async_chain._ensure_async_imports`. (Mitigated today by the eager
    main-thread call in `__main__`.)
  - `_proxy` failover captures `now` once before the loop (`rpc.py:826`) —
    after a 15 s primary timeout the fallback's cooldown stamp is already
    outside `_FAIL_FAST_S`. Re-read the clock per iteration. (Logic bug,
    not a race; found in passing.)
- **5f. Leaks (not races):** tx-details / composer dialogs connect to the
  shared `IconCache.icon_ready` and are never destroyed (no
  `WA_DeleteOnClose`, no ref kept) — they accumulate for the app lifetime;
  `_call_on_confirm` listeners for txs that end up dropped are never
  disconnected.
- **5g. Implicit thread-safety worth a comment:** `finished`-signal lambdas
  that mutate GUI-owned sets (`icons.py:412-415`,
  `plugins/tokens.py:791-792`, `plugins/transactions.py:1760-1761`) may run
  on the dying worker thread; each is a single GIL-atomic op today, so
  safe — but adding iteration/logging to any of them creates a real race.
  Empirically PySide6 ran a main-thread-connected lambda on the main
  thread in one test, so the affinity is uncertain — prefer queued bound
  methods or leave a warning comment.

---

## Audited and refuted (no action)

- Worker-set tracking: compliant everywhere checked — everything routes
  through `MainWindow.start_worker` or dialog-owned tracked sets; the one
  violation found is ENS `_start`'s fallback (3h).
- Signal payload types: all wei/balance/block payloads ride
  `Signal(object)`; chain ids ride `QULONGLONG`.
- `LiveWatcher` cross-thread handoff: immutable atomically-swapped
  snapshots, `threading.Event` stop, bounded join; 77e8994's task drain is
  correct.
- Old-account/chain results in tokens: apply paths write to caches keyed by
  the spawn-time (chain, account) and gate rendering on `_is_current_view`
  / `pv["view_key"]` — correct.
- Shared `requests.Session`: double-checked locking is benign under the
  GIL; urllib3 pools and CookieJar are internally locked; per-client
  failover state is not shared across threads.
- rpc.py loop-confined state (`_rpc_chain_id_by_origin`,
  `_ws_subscriptions`, `_pending_chain_add`): all Qt-thread entry points
  marshal via `run_coroutine_threadsafe`; `add_chain` re-checks under the
  store lock so the approval-await TOCTOU can't double-add. Concurrent
  `ws.send_str` frames can't interleave mid-write on one loop.
- Broadcast paths: concurrent rebroadcast of the same raw bytes is
  idempotent ("already known"); all three paths pin the user's RPC.
- `SignerBridge` double resolution: guarded by `fut.done()`.
- Ledger HID service ordering/exceptions: correct; FIFO starvation
  (a 180 s signing job stalls discovery) is by design — document.
- Helios: checkpoint self-heal is an argv flag at spawn (nothing to race);
  `_free_port` TOCTOU self-heals via the alive-check respawn.
- Icon memo / identity cache / tokenlists index: thread-confined or
  idempotent-immutable; `TokenLists.load` publishes a fresh dict by single
  assignment. (Latent: `load()` holds its RLock across the whole network
  fetch — any new GUI-thread caller of `addresses_for_chain` during a
  reload would freeze the UI. Today's only caller is the `loaded` slot.)
- Blockscout-fetch TTL caches: full-body rewrites, worst case duplicated
  work, never stale resurrection.
- Dapp-supplied `nonce` in `eth_sendTransaction` params is silently ignored
  (recomputed) — intentional-looking; document it.

---

## Suggested commit order

1. **P1 nonce pair** (1a + 1b) — funds-affecting, tiny diffs.
2. **P1 confirm multi-fire** (1c) + false-drop rate limit (1d).
3. **P1 signing UX/bridge** (1e + 1f).
4. **chain.py co-read** (P2 step 0) — standalone fix for 2a/2b.
5. **BalanceLedger** (P2 steps 1–4) — one branch, anvil-tested.
6. **ENS epoch** (P3 step 1) — tiny, immediate win.
7. **ENS block-stamp migration** (P3 steps 2–5).
8. **Single-instance lock + Store.save fixes** (P4).
9. **P5 batch** as touched.
