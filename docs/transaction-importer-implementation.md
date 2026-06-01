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

YNAB split parents carry `category_name == "Split"` and a `subtransactions[]` list; the
parent has no real category. LM's `InsertTransactionObject` has **no** split field
(`extra="forbid"`); splits are created via a separate split call. Two options:

- **(A) Flatten** — emit one `InsertTransactionObject` per subtransaction: amount/category
  from the sub, payee/date from the parent, `external_id = <sub.id>`,
  `custom_metadata = {ynab_id: sub.id, ynab_parent_id: parent.id}`. Skip the parent.
  Preserves per-category reporting; loses the visual "split" grouping in LM.
- **(B) Native split** — insert the parent, then issue LM's split request with
  `SplitTransactionObject[]`. Preserves grouping but needs a second API round-trip and a
  post-insert ID, complicating dedup and the DirSink format.

**Plan: implement (B) Native split.** Grouping (a possible Option C) was ruled out:
groups suppress children's categories from the budget engine — only the group parent's
category is counted. Native split is the only way to get both visual grouping and correct
per-category budget reporting.

Implementation uses **two passes**:

**Pass 1 — insert parent transactions.**
Insert each YNAB split parent as a single regular LM transaction with a special
"Incomplete Split" category (a dedicated LM category created during Phase 0, excluded from
budget and totals). Fields: amount = sum of subtransactions, payee/date from parent,
`external_id = <parent.id>`, `custom_metadata = {ynab_id: parent.id}`. This pass is safe
to re-run: already-inserted parents are detected via `external_id` dedup.

**Pass 2 — split the incomplete transactions.**
Query LM for all transactions in the "Incomplete Split" category. For each, call
`POST /transactions/split/{id}` with one `SplitTransactionObject` per YNAB subtransaction
(amount, category_id, payee, notes). On success, store the returned child LM IDs in
sync_state keyed by `ynab_subtransaction_id`. Pass 2 is also re-runnable: a parent with
`is_split_parent: true` (or whose children are already in sync_state) is skipped.

Other notes:
- "Incomplete Split" category is created in Phase 0 alongside other special categories.
- DirSink must represent both passes: a parent row (with `incomplete_split: true`) and a
  separate `split_pass` list of `{lm_parent_id, subtransactions[]}` records.
- Flag in the dry-run summary as `splits_native: N`.

## 5. YNAB → LM conversion (the field mapping)

| LM `InsertTransactionObject` | Source | Notes |
|---|---|---|
| `date` | YNAB `date` | already ISO 8601 |
| `amount` | `ynab.amount / 1000` | milliunits → decimal. YNAB sign convention already matches LM (negative = inflow/credit, positive = outflow/debit) — **verify against one real example before locking in**; the decision-table doc and `api-reference.md` disagree on wording. **Open Question 2.** |
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
3. Required special categories must exist: `sync.special_cat_id("payment_transfer")` and,
   if any off-budget account is involved, `"tracking_off_budget"`.

Behaviour on violation is controlled by `--on-missing {abort,skip,create}` (default `abort`).
`abort` prints the offending entities and exits non-zero. `skip` drops those txns into a
`skipped_missing` bucket. `create` is out of scope for v1 (categories/accounts are Phase 0's job).

## 7. Dedup & crash resistance

- **Manual accounts**: set `external_id = <ynab txn id>`. LM rejects a duplicate `external_id`
  on the same manual account and reports it under `skipped_duplicates` with
  `Reason.duplicate_external_id`. So re-runs are safe with **zero pre-fetch**. The sink just
  reports the skip counts.
- **Writable Plaid accounts** (rare): `external_id` uniqueness isn't enforced there, so the
  ApiSink pre-fetches existing transactions for that account and filters by
  `custom_metadata.ynab_id`. `existing_ynab_ids()` on the sink abstracts this.
- Transaction IDs are **not** recorded in `sync_state.json` (would bloat it to 10k+ entries);
  dedup relies on `external_id` + `custom_metadata.ynab_id` living on the LM side. (If a future
  tool needs the mapping, derive it by reading LM transactions back.)

## 8. The sink abstraction (`sinks.py`)

```python
@dataclass
class InsertResult:
    inserted: int
    skipped: int
    skipped_reasons: dict[str, int]   # Reason -> count (API); {} for DirSink

class TransactionSink(Protocol):
    def existing_ynab_ids(self, *, manual_account_id: int | None,
                          plaid_account_id: int | None) -> set[str]: ...
    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult: ...
    def close(self) -> None: ...
```

### `ApiSink`

Wraps `LMClient`. `insert()` delegates to `client.insert_transactions()` (already batches by
500 and aggregates `skipped_duplicates`). `existing_ynab_ids()` returns `set()` for manual
accounts (external_id handles it) and the real set for writable Plaid.

### `DirSink`

Writes LM-format JSON to `--to-dir DIR`, **no network at all**:

```
<DIR>/
  transactions.json   # JSON array of InsertTransactionObject dicts (exclude_none),
                       # in deterministic order (sorted by external_id) for stable diffs
  summary.json        # bucket counts from TransactionPlan
```

`existing_ynab_ids()` returns `set()` (fresh dump) — or, optionally, reads back its own prior
`transactions.json` so re-running into the same dir is idempotent (nice for tests).
`insert()` accumulates; `close()` flushes once, sorted, so output is reproducible.

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
  account, uncategorized inflow, balance adjustments) and split flattening.
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

## 12. Open questions (must resolve before `--apply` on production)

1. **Splits**: flatten (A) vs native LM split call (B). Start with (A); verify the v2 split
   endpoint before committing to (B).
2. **Amount sign**: confirm YNAB-milliunit → LM-decimal sign with a real round-trip example;
   `transaction-import-plan.md` says "negate sign" while `api-reference.md` implies the
   conventions already align. One authoritative test fixture settles it.
3. **Transfer-leg metadata** (Open Question 2 in `transaction-import-plan.md`): which of
   memo/flags/cleared/approved to carry per-leg vs into `custom_metadata`.
4. **`--since` + categories** (Open Question 3 there): category balances can't be reconstructed
   from a synthetic opening entry the way account balances can; document the limitation.
5. **Writable-Plaid dedup**: confirm `external_id` is truly not honored on Plaid inserts (drives
   whether `existing_ynab_ids` pre-fetch is needed at all).
