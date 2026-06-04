# Transaction Importer — Implementation Plan

This is the **code architecture** plan for Phase 1 (transactions). It complements:

- [transaction-import-plan.md](transaction-import-plan.md) — the *classification decision table* (semantics: which YNAB txn maps to what).
- [migration-plan.md](migration-plan.md) — the phased overview and crash-resistance contract.

The headline goal here, beyond "import transactions": **make the importer able to emit
LM-format transaction data to a directory instead of the API**, so the whole classify +
convert pipeline can be unit-tested with fixtures and golden files, never touching Lunch Money.

## 1. Design principle: split "classify + convert" from "write"

The current phases (`phase_accounts`, `phase_categories`) call `client.create_*` inline.
For transactions we separate two concerns that today are entangled:

1. **Pure core** — given YNAB transactions + a `SyncState`, produce a list of
   `InsertTransactionObject` (LM format) plus bucket counts. No I/O, no network.
   This is the entire decision table from `transaction-import-plan.md`, and it is the
   thing worth testing.
2. **Sink** — takes the produced `InsertTransactionObject`s and *writes* them, either to
   the LM API or to a directory as JSON. Selected at runtime by a CLI flag.

```
YNAB txns (dict) ──► build_transaction_plan() ──► TransactionPlan ──► sink.insert()
   (fixtures)         PURE, fully testable          (LM objects)      ApiSink | DirSink
                            ▲
                       SyncState (in-memory in tests; loaded from disk in prod)
```

Unit tests exercise `build_transaction_plan()` directly (assert on the LM objects), and/or
run the whole `import transactions` command against an export fixture with a `DirSink` and
diff the emitted JSON against a checked-in golden file.

## 2. New module layout

```
lunchmoney/
  transactions.py     # NEW — pure core: classification + YNAB→LM conversion
  sinks.py            # NEW — TransactionSink protocol + ApiSink, DirSink
  import.py           # phase_transactions() wires core → sink; CLI flags
tests/                # NEW
  fixtures/           # tiny hand-written YNAB export dirs + expected LM JSON (golden)
  test_transactions.py
  conftest.py
```

Keep the pure core (`transactions.py`) import-light: it must not import `lm_client` or do
any file/network I/O, so tests load fast and stay deterministic.

## 3. The pure core (`transactions.py`)

### Options

```python
@dataclass(frozen=True)
class TxnImportOptions:
    since: date | None = None                  # --since cutoff (opening balances ignore it)
    opening_balance_category: int | None = None # --opening-balance-category override
    deferred_income_as: str | None = None       # income | uncategorized | skip (case 9)
```

### Output

```python
BUCKETS = (
    "income", "uncategorized", "transfer_paired", "transfer_one_sided",
    "opening_balance", "tracking", "balance_adjustment",
    "skipped_zero", "skipped_deleted", "skipped_before_since", "needs_decision",
)

@dataclass
class ClassifiedTxn:
    ynab_id: str
    bucket: str
    insert: InsertTransactionObject | None   # None for skipped/needs_decision buckets
    note: str = ""                           # human reason, shown in dry-run / needs_decision

@dataclass
class TransactionPlan:
    items: list[ClassifiedTxn]
    counts: dict[str, int]                   # bucket -> count
    needs_decision: list[ClassifiedTxn]      # non-empty ⇒ abort unless options resolve them
```

### Entry point

```python
def build_transaction_plan(
    ynab_txns: list[dict[str, Any]],
    *,
    sync: SyncState,
    options: TxnImportOptions,
) -> TransactionPlan: ...
```

All lookups go through `SyncState` (already in memory): `sync.lm_account_id(ynab_acct)`,
`sync.lm_category_id(ynab_cat)`, `sync.special_cat_id("payment_transfer" | "tracking_off_budget")`,
`sync.ynab_internal_cat("inflow" | "uncategorized")`. This is exactly why tests can build a
`SyncStateData(...)` by hand and never touch disk or API.

### Per-transaction algorithm (implements the decision table)

For each YNAB txn (and each subtransaction — see §4):

1. `deleted` → bucket `skipped_deleted`, no insert. (Exported for completeness, never imported.)
2. Compute flags: `is_transfer`, `is_starting_balance`, `is_zero`, `cat_name`,
   `src_account_off_budget` (from the YNAB account's `on_budget` flag, available in `accounts.json`).
3. Resolve the **destination LM account**: `sync.account(account_id)`.
   - `lm_type == "skipped"` or no entry → the account wasn't migrated. Transfers become
     `transfer_one_sided` (import only this leg if *this* account is migrated; here it isn't,
     so skip). Non-transfers on an unmigrated account are a pre-flight failure (see §6) —
     should never reach the core.
4. Apply the decision table to pick **category + bucket** (see `transaction-import-plan.md`):
   - Starting Balance + zero → `skipped_zero`.
   - Starting Balance + non-zero → `opening_balance`, category = `options.opening_balance_category`
     or null, `custom_metadata.ynab_starting_balance=true`, **ignore `--since`**.
   - Transfer (either leg) → `transfer_paired` (both accounts migrated) or `transfer_one_sided`;
     category = "Payment, Transfer"; set `custom_metadata.ynab_paired_id`.
   - `Inflow: Ready to Assign`, non-transfer → `income`, mapped income category.
   - `Uncategorized`, non-transfer → `uncategorized`, null category, `ynab_uncategorized=true`.
   - Source account `on_budget == false` → override category to "Tracking (off-budget)",
     bucket `tracking` (transfers keep "Payment, Transfer").
   - Balance-adjustment payees → `balance_adjustment`, null category, preserve payee.
   - `Deferred Income SubCategory` → resolve via `options.deferred_income_as`; if None →
     `needs_decision`.
   - Otherwise normal spending/income → mapped category; bucket `income` if positive on an
     income category else `uncategorized`/normal.
5. `--since`: if `options.since` and `date < since` and bucket != `opening_balance` →
   `skipped_before_since`.
6. Build the `InsertTransactionObject` (see §5).

`build_transaction_plan` is the function with ~all the branches and ~all the tests.

## 4. Splits (subtransactions)

> **Decision (resolved):** import **all** splits as **native LM splits** (two-pass). Do not
> flatten. Transfer and uncategorized subtransactions are handled inside the native flow
> with the documented limitations below — they are common (in `data/cad`: **76 of 351**
> splits contain a transfer sub, **41** contain an uncategorized sub) so they are not edge
> cases and the implementer must handle them explicitly.
>
> **Why native (not flatten):** the split **parent keeps the real, single charge amount**
> (e.g. one `$53.35` purchase), so it matches the corresponding line on a bank/Plaid
> statement during reconciliation. Flattening into per-category transactions
> (`$33.35` + `$20.00`) destroys that 1:1 correspondence and makes the original charge
> unidentifiable against the statement. This is a hard requirement — it applies to **every**
> split, including transfer-containing ones (the parent still matches the statement line even
> though one child is a `"Payment, Transfer"`).

### 4.1 YNAB shape and LM API facts

YNAB split parents carry `category_name == "Split"`, a real `category_id` pointing at YNAB's
internal "Split" pseudo-category, and a non-empty `subtransactions[]` list. Each sub has its
own `id`, `amount`, `category_id`/`category_name`, optional `memo`/`payee_name`, and an
optional `transfer_account_id`/`transfer_transaction_id` (a transfer leg embedded in the
split). Sub amounts always sum exactly to the parent amount (verified across `data/cad`).

LM's `InsertTransactionObject` has **no** split field (`extra="forbid"`). Splits are created
with a dedicated call on an already-inserted parent:

- `POST /transactions/split/{id}` — body `{ "child_transactions": [ <splitTransactionObject>, ... ] }`.
- `child_transactions`: **min 2, max 500**. The **sum of child `amount`s must equal the parent
  transaction amount** or LM returns `400` *"Sum of split transactions do not add up…"*. (Use
  the same YNAB→LM sign conversion as §5 for both parent and children so the sums match.)
- `splitTransactionObject` fields are **only** `amount` (required), `payee`, `date`,
  `category_id` (**`int32`, no `null`**), `notes`, `tag_ids`. `additionalProperties: false` —
  **there is no `external_id` and no `custom_metadata` on a split child.** This is the central
  constraint that shapes everything below.
- After a transaction is split, the parent **disappears** from `GET /transactions` and the
  children are returned instead. To see a split parent again, pass `include_split_parents=true`
  (and `include_children=true` to get its children), or `GET /transactions/{parent_id}`.
- Cannot split a recurring, group, or already-split transaction (LM `400`s each).

### 4.2 Two-pass algorithm

**Pass 1 — insert parents.** Insert each YNAB split parent as one regular LM transaction:
- `category_id` = the **"Incomplete Split"** special category (new in Phase 0 — see §4.4),
  which is `exclude_from_budget` + `exclude_from_totals` so an un-split parent never pollutes
  budget/totals in the window between the two passes.
- `amount` = parent amount (= sum of subs), `payee`/`date`/`notes` from the parent,
  `external_id = <parent.id>`, `custom_metadata = {ynab_id: parent.id, ynab_is_split_parent: true}`.
- Capture the created LM id from the POST response (`response.transactions[]`, matched by
  `external_id`) and record it in `sync_state.split_parents[parent.id] = {lm_id, split_done: false}`.
- Re-run safe: a duplicate `external_id` is reported in `skipped_duplicates`; recover the LM id
  by reading it back (or it is already in `sync_state`).

**Pass 2 — split each parent.** For every `sync_state.split_parents` entry with
`split_done == false`, build `child_transactions` (one per **non-deleted** sub) and call
`POST /transactions/split/{lm_id}`:
- **Normal sub** → `category_id` = mapped LM category (`sync.lm_category_id(sub.category_id)`).
- **Transfer sub** (`sub.transfer_account_id` set) → `category_id` = `payment_transfer` special
  category. The child carries no pairing metadata (schema forbids it); the pairing is recorded
  on the **other** leg instead — see §4.3.
- **Uncategorized sub** (`sub.category_id` null / "Uncategorized") → cannot be expressed in the
  split call (`category_id` has no `null`; omitting it makes the child *inherit* the parent's
  "Incomplete Split" category, which is wrong). Resolution: leave it categorized as the
  `uncategorized`-marker during the split, then immediately `PUT /transactions/{child_lm_id}`
  to clear `category_id` and set `custom_metadata.ynab_uncategorized = true`. **(Open Question 1
  — verify `PUT` accepts `category_id: null` to uncategorize.)**
- On success, record each child: `sync_state.split_children[sub.id] = child_lm_id` (matched to
  subs by order + amount in the returned `children[]`), then set `split_done: true` and save.

**Re-run / crash handling for Pass 2.** Primary signal is `split_done` in `sync_state`. If a
split succeeded on LM but we crashed before recording it, the parent is gone from
`GET /transactions` and `split_done` is still false; re-issuing the split returns `400`
*"cannot split an already split transaction."* Treat that specific `400` as success: fetch the
existing children via `GET /transactions/{lm_id}?include_split_parents=true&include_children=true`,
backfill `split_children`, and set `split_done: true`.

### 4.3 Transfer subs and partial pairing

A transfer embedded in a split cannot be a true cross-account leg in LM (a split child is bound
to the parent's account and carries no metadata — see §4.1). It is therefore imported as a
`"Payment, Transfer"` **child** on the parent's account. The **counterpart** leg is always a
**top-level** YNAB transaction on the other account (structural YNAB fact: a split transfer
line's other side is never itself a split sub — verified: 0 of 110 such legs target a sub), and
it is imported normally with `external_id = <ynab_id>` and
`custom_metadata.ynab_paired_id = <sub.id>`. So:

- **Pairing metadata is preserved** on the resolvable (top-level) side, and `sub.id` resolves to
  the child's LM id via `sync_state.split_children`.
- **Grouping may be partial**: the Transfer Management Tool can *identify* both pairs, but may be
  unable to *group* a split child into an LM transaction group (LM constraint — to verify). This
  limitation is documented in [future-tools.md](future-tools.md).

### 4.4 Supporting changes (implementer checklist)

- **`lunchmoney/import.py`** — add an `incomplete_split` entry to `SPECIAL_LM_CATS`
  (`{"name": "Incomplete Split", "exclude_from_budget": True, "exclude_from_totals": True}`).
  It then flows through the existing `_build_special_cat_plan` / `phase_categories` machinery
  (create / recover / sync) with no other Phase 0 changes.
- **`lunchmoney/sync_state.py`** — add `split_parents: dict[str, {lm_id:int, split_done:bool}]`
  and `split_children: dict[str, int]` to `SyncStateData`, with accessors. Update the
  `special_categories` doc to list `incomplete_split`. These two maps are bounded by the split
  count (~351), not the full transaction count — see the §7 exception.
- **`lunchmoney/lm_client.py`** — add `split_transaction(transaction_id: int,
  child_transactions: list[SplitTransactionObject]) -> TransactionObject` (POST
  `/transactions/split/{id}`), returning the parent with populated `children`.
- **DirSink** must represent both passes deterministically: the parent row in
  `transactions.json` (with `custom_metadata.ynab_is_split_parent`), plus a `split_pass.json`
  list of `{ynab_parent_id, child_transactions:[…]}` records (sorted by `ynab_parent_id`) so the
  whole split flow is golden-testable without the API.
- **Dry-run summary** buckets: `splits_native: N` (parents) and `split_children: M`.

## 5. YNAB → LM conversion (the field mapping)

| LM `InsertTransactionObject` | Source | Notes |
|---|---|---|
| `date` | YNAB `date` | already ISO 8601 |
| `amount` | `-ynab.amount`, ÷1000, 4dp string | **Negate** the sign, then milliunits→decimal. YNAB and LM use **opposite** sign conventions (YNAB negative = outflow/expense; LM positive = debit/expense). Use the single helper in **[amount-conversion.md](amount-conversion.md)** — `f"{-milliunits / 1000:.4f}"` — for every amount (parent, children, opening balances). For native splits, the child amounts must still sum to the parent amount *after* negation, so applying the same helper everywhere keeps the sums consistent. |
| `currency` | sync_state `currency` | lowercase ISO 4217 |
| `payee` | YNAB `payee_name` | preserve "Starting Balance", adjustment payees verbatim |
| `category_id` | resolved per decision table | null for opening balance / uncategorized |
| `manual_account_id` / `plaid_account_id` | from `sync.account(...).lm_type` | exactly one set |
| `notes` | YNAB `memo` | |
| `status` | derived from YNAB `approved` | `reviewed` if approved else `unreviewed` |
| `external_id` | YNAB txn `id` | **dedup key** — see §7 |
| `custom_metadata` | `{ynab_id, ynab_paired_id?, ynab_starting_balance?, ynab_uncategorized?, ynab_parent_id?, ynab_flag_color?}` | |

Use `exclude_none=True` when dumping so optional fields stay absent.

## 6. Pre-flight checks (gate before any classify)

Per the `cmd_import` TODO and the `project_transaction_import_deps` note: transactions
require Phase 0 done. Before building the plan:

1. Every non-deleted YNAB txn's `account_id` must resolve in `sync.accounts` to a non-skipped
   entry **or** be a transfer whose only-migrated leg we keep. Collect violations.
2. Every referenced `category_id` (incl. subtransactions) must resolve in `sync.categories`,
   be a known internal category (`sync.ynab_internal_cats`), or "Split".
3. Required special categories must exist: `sync.special_cat_id("payment_transfer")`; if any
   off-budget account is involved, `"tracking_off_budget"`; and if any split transactions are
   present, `"incomplete_split"` (§4.4).

Behaviour on violation is controlled by `--on-missing {abort,skip,create}` (default `abort`).
`abort` prints the offending entities and exits non-zero. `skip` drops those txns into a
`skipped_missing` bucket. `create` is out of scope for v1 (categories/accounts are Phase 0's job).

## 7. Dedup & crash resistance

The importer keeps a **local index** of every imported transaction in `sync_state.json`
(`transactions[ynab_id] = {lm_id, split_done}`). This is the dedup key — without it, every
run re-POSTed all ~12k transactions to LM and leaned on the server to reject them as
duplicates, which abuses the API and makes dry-runs lie ("would import 12068").

- **Building the index.** Before planning, `phase_transactions` calls
  `_reconcile_txn_index()`. If the index has never been built (`txn_index_built == False`)
  it scans LM once (`sink.scan_imported()`) and records every `ynab_id ↔ lm_id` pair found
  via `custom_metadata.ynab_id`. This is read-only, runs in dry-run too, and migrates an
  account that was imported before the index existed. `--rebuild-index` forces a discard +
  re-scan (`sync.clear_transactions()` then rebuild).
- **Planning.** `build_transaction_plan` stays pure; the phase then partitions importable
  items into *new* (`ynab_id not in synced_ids`) vs *already imported*, and the dry-run
  counts/summary reflect **only the new work**. Already-imported txns show under
  "Skip … already imported (in local index)".
- **Applying.** Pass 1 inserts only the new items. The insert result returns
  `id_by_external` (`ynab_id → lm_id`, populated from inserted txns *and* from any
  `skipped_duplicates` via `existing_transaction_id` — self-healing if the index was
  incomplete); those pairs are written to the index immediately. `external_id =
  <ynab txn id>` on manual accounts remains a second line of defense.
- **Split parents** dedup via the index like any other txn (Pass 1). Their `split_done`
  flag gates Pass 2 so the split is idempotent. `scan_imported()` recovers already-split
  parents in the same scan via `include_split_parents=true` (they come back flagged
  `is_split_parent`, which sets `split_done`). Split **children** carry no `external_id` /
  `custom_metadata` (LM schema forbids it — §4.1) and are never indexed directly; they're
  reconstructed from the parent on each run.
- **Crash window.** The index is saved after Pass 1 and after Pass 2. If a crash lands
  between a `split()` call and the save, `split_done` is stale; `--rebuild-index` reconciles
  it (the parent comes back `is_split_parent`).

## 8. The sink abstraction (`sinks.py`)

```python
@dataclass
class ScannedTxn:                          # one already-imported txn discovered on LM
    ynab_id: str
    lm_id: int
    split_done: bool = False

@dataclass
class InsertResult:
    inserted: int
    skipped: int
    skipped_reasons: dict[str, int]        # Reason -> count (API); {} for DirSink
    id_by_external: dict[str, int]         # ynab_id -> LM txn id (for the local index)

class TransactionSink(Protocol):
    def scan_imported(self) -> list[ScannedTxn]: ...   # rebuild the index from LM
    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult: ...
    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> None: ...
    def close(self) -> None: ...
```

Pass 2 no longer asks the sink to locate unsplit parents — the caller drives splits from the
local index (`sync.txn(parent).lm_id`, gated on `split_done`).

### `ApiSink`

Wraps `LMClient`. `scan_imported()` does one paginated `GET /transactions`
(`include_split_parents=true`, `include_metadata=true`) and returns a `ScannedTxn` per txn
carrying `custom_metadata.ynab_id`. `insert()` delegates to `client.insert_transactions()`
(batches by 500, aggregates `skipped_duplicates`) and builds `id_by_external` from the
inserted txns plus any skipped duplicates' `existing_transaction_id`. `split()` calls
`client.split_transaction()` (§4.4).

### `DirSink`

Writes LM-format JSON to `--to-dir DIR`, **no network at all**:

```
<DIR>/
  transactions.json   # JSON array of InsertTransactionObject dicts (exclude_none),
                       # in deterministic order (sorted by external_id) for stable diffs
  split_pass.json     # list of {parent_lm_id, child_transactions:[SplitTransactionObject...]}
                       # sorted by parent_lm_id — the Pass-2 split requests (§4.2)
```

`scan_imported()` returns `[]` (no persistent LM side in `--to-dir` mode; runs use fresh
dirs). `insert()` accumulates and returns synthetic sequential ids in `id_by_external`, so
the in-memory index drives Pass 2 the same way as `ApiSink`. `split()` appends a record to
`split_pass.json`. `close()` flushes once, sorted, so output is reproducible.

This is the artifact unit tests assert against.

## 9. CLI integration (`import.py`)

- Add `"transactions"` to `VALID_ENTITIES`.
- `phase_transactions(data_dir, sink, sync, sync_dir, options, apply, confirm_each)`:
  load `transactions.json` + `accounts.json` → pre-flight (§6) → `build_transaction_plan`
  → `_print_transaction_plan` (bucket table, matching the dry-run buckets in
  `transaction-import-plan.md`) → on `--apply`, feed inserts to `sink.insert()` in order,
  print inserted/skipped.
- New flags on the `import` subcommand:
  - `--to-dir DIR` — use `DirSink` writing LM JSON to DIR (selects sink; works with or
    without `--apply`; needs **no** `LUNCHMONEY_API_TOKEN`).
  - `--rebuild-index` — discard and re-scan the local already-imported index from LM (§7).
    The index is built automatically on the first run; this forces a refresh.
  - `--since`, `--opening-balance-category`, `--deferred-income-as`, `--on-missing` (above).
- Sink selection in `cmd_import`/`main`: `--to-dir` → `DirSink`; else `ApiSink(LMClient(token))`.
  When `--to-dir` is given, skip the `get_me()` call — instead require `--lm-account-id`
  (or read it from an existing `sync_state.json`) so the pure core has account IDs. This keeps
  the directory path completely token-free for tests/CI.

Accounts/categories phases stay on the direct-client path for now; only transactions go through
a sink. The `TransactionSink` protocol is deliberately narrow so a later refactor could give
accounts/categories the same treatment, but that's **not** in this scope.

## 10. Testing strategy

- **Unit (core)**: `test_transactions.py` builds a `SyncState` in memory and feeds small
  hand-written YNAB txn dicts to `build_transaction_plan`, asserting bucket + the resulting
  `InsertTransactionObject` fields. One test per decision-table row (cases 1–10), plus edge
  cases from `transaction-import-plan.md` §"Edge Cases" (negative income, transfer to deleted
  account, uncategorized inflow, balance adjustments). For splits (§4), assert the Pass-1 parent
  object (amount = sum of subs after negation, "Incomplete Split" category, `ynab_is_split_parent`)
  **and** the Pass-2 `child_transactions` (a normal sub, a transfer sub → "Payment, Transfer",
  an uncategorized sub → uncategorize follow-up) including that the child amounts sum to the
  parent amount.
- **Golden / end-to-end (DirSink)**: `tests/fixtures/<name>/` holds a minimal but complete
  export dir (`export_metadata.json`, `accounts.json`, `categories.json`, `transactions.json`)
  and a pre-populated `sync_state.json` (Phase 0 pretend-done). The test runs the `import
  transactions --to-dir <tmp> --apply` path and diffs `<tmp>/transactions.json` against
  `expected_transactions.json`. Regenerate goldens with an `UPDATE_GOLDENS=1` env switch.
- **Determinism**: sort emitted txns by `external_id`; freeze any timestamps. No network, no
  `LUNCHMONEY_API_TOKEN`, runs in CI.
- Add `pytest` to `requirements.txt` (not currently installed) and a `make test` target using
  `.venv/bin/pytest`.

## 11. Implementation order

1. `transactions.py` core + `TxnImportOptions`/`TransactionPlan` types; no sink yet.
2. Unit tests for the decision table against the core (red/green per row).
3. `sinks.py` with `DirSink` first (testable immediately), then `ApiSink`.
4. `phase_transactions` + CLI flags; wire `DirSink` path end-to-end.
5. Golden fixtures from a trimmed slice of `data/cad`; lock goldens.
6. `ApiSink` dry-run against `.env.testing`, then `--apply` on the test LM account.
7. Reconcile against `balance-reconciliation.md`; resolve open questions below.

## 12. Open questions

### Resolved

- ~~**Splits: flatten vs native.**~~ **Resolved: fully native, two-pass (§4).** Flattening is
  rejected because the split parent must keep the real single-charge amount to reconcile against
  a bank/Plaid statement line.
- ~~**Amount sign.**~~ **Resolved: negate (§5, [amount-conversion.md](amount-conversion.md)).**
  YNAB and LM use opposite conventions; confirmed against real data and the LM split example.

### Must resolve before `--apply` on production

1. **Uncategorized split children**: verify `PUT /transactions/{id}` accepts `category_id: null`
   to uncategorize a split child after the split call (§4.2). If not, find LM's supported way to
   leave a split child uncategorized, or accept it stays in "Incomplete Split".
2. **Grouping split children**: verify whether LM lets a split child join a transaction group
   (drives whether split-embedded transfers are groupable by the Transfer Management Tool, §4.3
   and [future-tools.md](future-tools.md)).
3. **Transfer-leg metadata** (Open Question 2 in `transaction-import-plan.md`): which of
   memo/flags/cleared/approved to carry per-leg vs into `custom_metadata`.
4. **`--since` + categories** (Open Question 3 there): category balances can't be reconstructed
   from a synthetic opening entry the way account balances can; document the limitation.
5. **Writable-Plaid dedup**: confirm `external_id` is truly not honored on Plaid inserts (drives
   whether `existing_ynab_ids` pre-fetch is needed at all).
