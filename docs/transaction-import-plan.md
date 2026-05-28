# Transaction Import — Internal Category Handling Plan

## Pre-classification

For each YNAB transaction, compute these flags first:

```python
is_transfer = txn.transfer_account_id is not None
is_starting_balance = txn.payee_name == "Starting Balance"
is_zero = txn.amount == 0
cat_name = txn.category_name  # may be None
```

## Decision table

| # | Case | Action | LM `category_id` | Notes |
|---|------|--------|------------------|-------|
| 1 | `Inflow: Ready to Assign` + transfer (inflow side) | **Skip** (handled by outflow side, see Transfer Strategy) | — | Record `ynab_id` → `lm_transfer_id` in sync_state so dedup works on re-runs |
| 2 | `Inflow: Ready to Assign` + non-transfer income | **Import** | Mapped LM income category (`is_income=True`); looked up during Phase 0 | Canonical income mapping |
| 3 | `Inflow: Ready to Assign` + Starting Balance + zero | **Skip** | — | Pure YNAB metadata |
| 4 | `Inflow: Ready to Assign` + Starting Balance + non-zero | **Import as opening balance** | null (or `--opening-balance-category`) | Set `custom_metadata.ynab_starting_balance=true`; payee = "Starting Balance" |
| 5 | `Uncategorized` + transfer (outflow side) | **Import as transfer** (see Transfer Strategy) | null | Do not map "Uncategorized" to any LM category |
| 6 | `Uncategorized` + regular spending | **Import** | null | Tag `custom_metadata.ynab_uncategorized=true` so user can filter & clean up post-import |
| 7 | `Uncategorized` + Starting Balance + zero | **Skip** | — | Pure metadata |
| 8 | `Uncategorized` + Starting Balance + non-zero | **Import as opening balance** | null | Same as case 4; negative amount handles liability accounts |
| 9 | `Deferred Income SubCategory` on any txn | **Warn + require user decision** | — | Per CLAUDE.md: offer choices — (a) treat as income, (b) treat as uncategorized, (c) skip. Cache decision in sync_state |
| 10 | `Credit Card Payments` group categories | **Warn + require user decision** | — | Should not occur in practice. If found: treat as uncategorized + flag for review. Never auto-map |

## Transfer Strategy (cases 1 & 5)

YNAB stores two paired transactions per transfer; LM stores **one** transfer with two sides.

- **Process outflow side only** (case 5). The inflow side (case 1) is skipped, but its `ynab_id` is recorded as paired to the same LM transfer for dedup.
- Match LM accounts: source = `txn.account_id` (mapped via sync_state), destination = `txn.transfer_account_id` (mapped via sync_state).
- If the destination account was **not migrated** (excluded, or Plaid read-only):
  - Import the outflow side as a **regular transaction** with null category and payee = original "Transfer : X" string.
  - Skip inflow side if its account also was not migrated; otherwise import inflow independently as a regular transaction.
- Pairing key: sort `(ynab_id_a, ynab_id_b)` lexically; the lower one "owns" the LM transfer. Record both `ynab_id`s in `custom_metadata` (`ynab_id`, `ynab_paired_id`).

## Opening Balance Handling (cases 4 & 8)

- Import as a one-sided transaction in LM with the YNAB `date` and amount.
- If `--since` cutoff is later than the Starting Balance date: **still import** (date-filter exception) so reconciliation works. See [balance-reconciliation.md](balance-reconciliation.md).
- Payee = "Starting Balance"; LM category = null by default.
- Shown as a separate count in the dry-run summary: `opening_balances: N`.

## Dry-Run Summary Buckets

```
transactions:
  income (Inflow: Ready to Assign):     N
  uncategorized (regular):              N
  uncategorized (flagged for review):   N
  transfers (paired, both migrated):    N
  transfers (one-sided, dest excluded): N
  opening balances:                     N
  skipped (zero-amount starting bal):   N
  skipped (deleted):                    N
  needs user decision (Deferred Inc.):  N
  needs user decision (CC Payments):    N
```

Abort if any "needs user decision" bucket is non-zero and no resolution is cached in sync_state.

## Edge Cases

1. **Negative income** (refund on `Inflow: Ready to Assign`, non-transfer): import as income with negative amount; LM accepts this. Do not flip sign.
2. **Transfer to a deleted account**: treat as one-sided regular transaction.
3. **Cross-currency transfers** (e.g. BRL ↔ CAD): YNAB stores these as two independent transactions with different amounts (no FX field). Import as **two unpaired regular transactions** with null category and `custom_metadata.ynab_cross_currency_transfer=true` — let the user reconcile post-import via the Transfer Management Tool.
4. **"Uncategorized" on inflow (positive amount, no transfer)**: rare (income never categorized in YNAB). Treat as case 6 (null category + flagged), **not** as income — preserves user intent.
5. **Re-run safety**: every imported txn must carry `custom_metadata.ynab_id`; transfer pairs also carry `ynab_paired_id`. Lookup before insert; rely on LM's `skipped_duplicates` as a second line of defense.

## Config Knobs

- `--map-uncategorized-to <category_id>`: override case 6 default (null) to a specific LM category.
- `--opening-balance-category <category_id>`: override cases 4/8 to use a specific category instead of null.
- `--deferred-income-as {income,uncategorized,skip}`: pre-answer case 9 non-interactively.

---

## Credit Card Transactions

YNAB's CC budgeting model (auto-generated "Credit Card Payment" categories, budget-side fund shifts) is a pure budgeting abstraction with no transaction-level footprint. CC accounts behave like any other liability account for import — no special-casing required at the transaction level.

### Decision table

| YNAB transaction shape | LM treatment |
|---|---|
| Expense on CC account with real category (Groceries, Uber Eats, etc.) | Import as normal expense on the LM CC (liability) account. Map category via sync_state. No special handling. |
| CC payment: transfer checking → CC, `category='Uncategorized'`, `payee='Transfer : ...'` | Process outflow (checking) side only per the transfer strategy. Skip CC-side leg, record both `ynab_id`s in `custom_metadata`. Category = null. |
| `cat='Inflow: Ready to Assign'`, `amount=0`, `payee='Starting Balance'` on CC | Skip (existing rule). |
| `cat='Inflow: Ready to Assign'`, `amount≠0`, `payee='Starting Balance'` on CC | Import as opening balance on the LM CC account (existing rule). |
| `cat='Inflow: Ready to Assign'`, `amount≠0`, `payee='Manual Balance Adjustment'` or `'Reconciliation Balance Adjustment'` on CC | Import as plain transaction, null category, preserve payee text. Flag in dry-run summary. Verify sign convention: positive on a YNAB liability account = balance reduction. |
| Refund/return on CC (positive-amount expense with real category) | Import as-is — LM handles positive amounts on liability accounts fine. |

### CC payment direction (confirmed)

The outflow side for CC payments is always the **checking side** (negative amount leaving checking). The existing "process outflow only" transfer logic picks this up correctly — no code change needed.

Caveat: if reconciliation shows CC balance drift after import, the fallback is to import both legs and use LM native transfer pairing instead.

### "Credit Card Payment" budget categories

The entire "Credit Card Payments" group was skipped in Phase 0 (correct). Zero transactions reference these categories. No action needed.

Defensive check: if any transaction has `category_group_name == 'Credit Card Payments'`, log a warning and import with null category rather than failing.

### Other CC edge cases

- **Foreign-currency CCs**: the account-exclusion rules (multi-budget.md) handle this at the account level. Ensure transfer-skip logic checks exclusion on *both* legs.
- **Balance adjustments**: grep for `payee LIKE '%Balance Adjustment%'` to catch both YNAB variants.
- **CC interest charges**: usually manual expenses with a real category. No special handling; flag in dry-run so user can verify reconciliation.

---

## Gaps and Open Questions

This section captures known weaknesses, missing definitions, and incorrect claims in the rest of this document. Each item is to be addressed in a follow-up revision of the plan; do not treat the sections above as final until these are resolved.

### 1. "Deferred Income" semantics are undefined
Case 9 in the decision table defers to the user, but the doc never defines what YNAB's "Deferred Income SubCategory" actually represents (income earned in one month but intended for a future month's budget), when YNAB auto-creates it vs. when the user does, or whether it can carry a non-zero balance across the cutoff date. Need to research the YNAB behaviour and document a canonical mapping recommendation before asking the user to choose.

### 2. CC-payment-category absence must be **asserted**, not assumed
Case 10 and the "Defensive check" in the CC section both say "should not occur" / "log a warning" — too soft. Implementation must pre-flight scan the entire YNAB export and **abort with a clear error** if any transaction carries `category_group_name == 'Credit Card Payments'`. Soft warnings hide reconciliation-breaking data.

### 3. Concrete fallback if the CC-payment-category assumption fails
If a real export does contain CC-payment-categorised transactions, the doc has no plan. Options to evaluate:
  - (a) Treat as a transfer from the budget category to the CC account (closest to YNAB's intent).
  - (b) Import as uncategorised + flag for manual review.
  - (c) Abort and require the user to fix the source data in YNAB.
Pick one as default, document why, and surface the others as escape hatches in config.

### 4. Transfer-leg metadata merge rules are missing
The pairing strategy covers `ynab_id` / `ynab_paired_id` but is silent on per-leg metadata: memo/notes, flags, cleared status, approved status, original payee strings. Both YNAB legs can carry different values; LM stores one transfer. Need explicit precedence rules (e.g. outflow side wins; concatenate conflicting memos; preserve both flag colors in `custom_metadata`).

### 5. In-budget vs. off-budget account handling is unspecified
YNAB distinguishes on-budget from off-budget ("Tracking") accounts; transactions on tracking accounts don't affect category balances. LM's equivalent (excluding accounts from budget calculations) needs to be set per account, and transactions on tracking accounts must not be assigned categories that would distort LM's category totals. The plan currently treats all accounts identically.

### 6. No method for verifying category balances match post-import
Account balance reconciliation is documented (balance-reconciliation.md) but there's no equivalent for category balances. Need: a reconciliation pass that sums imported transactions per LM category and compares against YNAB's category activity for the same date range, plus a documented tolerance and how to surface discrepancies.

### 7. Zero-based budgeting reproduction is unaddressed
YNAB enforces every-dollar-assigned; LM does not by default. The plan doesn't describe how transaction-level metadata (and the eventual Phase 2 budget assignments) will reproduce or approximate zero-based behaviour in LM. Without this, category balances will diverge even when transactions are imported correctly.

### 8. User opt-out for zero-based budgeting
Some users migrate to LM specifically to escape zero-based budgeting. There should be a config option to suppress zero-based reconstruction (skip Phase 2 budget assignments, don't synthesize per-category opening entries, etc.). This needs to be a first-class choice, not an undocumented side effect.

### 9. Category balance reconstruction under partial import (`--since`)
Account balances are repaired via opening-balance transactions, but categories have no "opening balance" concept in YNAB. When history before the cutoff is excluded, category balances cannot be reconstructed the same way. Options to evaluate: synthesize per-category opening entries, accept lost pre-cutoff history, or refuse partial imports when zero-based mode is on. Pick one and document tradeoffs.

### 10. Late-arriving (earlier) transactions break opening balances
If a user runs `--since 1y` and later backfills older history, the synthesised opening-balance transactions will double-count. Need: a documented backfill procedure, a `sync_state` field tracking the current cutoff date, and detection logic that adjusts or removes opening-balance entries when an earlier cutoff is requested.

### 11. Monthly carryover behaviour must be reconciled
YNAB carries unspent / overspent category balances forward according to per-category rules (cash vs. credit, with/without "overspending handling"); LM has different semantics. Need to document YNAB's carryover rules per category type, the equivalent LM settings, and how the importer configures LM to match. Without this, balances drift month over month even when individual transactions are correct.

### 12. Cross-currency transfer edge case is **wrong** and must be removed
Edge case #3 claims YNAB stores cross-currency transfers as two transactions with different amounts. **YNAB has no cross-currency transfer support at all** — every transfer is single-currency by definition. Cross-currency movement between budgets is represented as two unrelated transactions in two separate YNAB budgets, which is out of scope for a single-budget importer and is already handled at the account-exclusion level (see multi-budget.md). Delete the edge case; do not introduce `ynab_cross_currency_transfer` metadata.

### 13. Re-sync after the user has been actively using LM (deferred)
If the user merges LM accounts, moves transactions between accounts, or splits/merges categories after the initial import, the sync_state mapping goes stale and re-runs will misbehave. Marked as a **future feature, not v1**. The plan should explicitly state that re-running the importer against a "live" LM account isn't supported in the initial release, and list the known failure modes for the future implementer.

### 14. No CLI knobs for behaviour — config file only
The current "Config Knobs" section uses `--map-uncategorized-to`, `--opening-balance-category`, `--deferred-income-as` flags. Move all behaviour configuration into a config file (TOML or YAML). The CLI should only take a path to the config file plus operational flags (`--dry-run`, `--since`, `--apply`, etc.). Rationale: these decisions are per-import-pair, repeated across runs, and belong in version-controllable state — not in shell history.

### 15. Debt accounts have unreconcilable balances without a synthetic interest entry
Per [mortgage-debt-tracking.md](mortgage-debt-tracking.md), YNAB debt accounts (mortgage, auto loan, line of credit) embed accrued interest into the account `balance` with **no corresponding transactions**. The transaction-import plan currently has no special handling for this: a transaction-by-transaction import of a debt account will leave its LM balance short by the accumulated interest. The plan needs to specify:
  - Detection: identify YNAB accounts of debt type (or with non-empty `debt_interest_rates`).
  - Reconciliation: after importing transactions, compute `expected_balance − sum(imported_txns) − opening_balance` and, if non-zero, insert a synthetic "Accrued Interest" adjustment transaction dated at the export-as-of date (or import time). Tag it with `custom_metadata.ynab_synthetic_interest_adjustment=true` so re-runs can find and replace it.
  - Re-run behaviour: if a later run detects a different interest gap, **update** the existing synthetic transaction rather than appending a new one. (The opening-balance adjustment mechanism from item 10 is the natural pattern to mirror.)
  - Metadata preservation: store `debt_interest_rates`, `debt_minimum_payments`, `debt_escrow_amounts`, and any `debt_transaction_type='balanceAdjustment'` markers on the LM account `custom_metadata`. These do not affect transaction logic but are needed for audit and future tooling.
  - Edge cases: variable-rate LOCs (rate dictionary changes mid-cutoff), accounts with payments before the `--since` cutoff (the synthetic adjustment must absorb pre-cutoff interest *and* pre-cutoff principal alongside the opening balance — work out the math and document it), and YNAB's own `balanceAdjustment` debt-transaction-type entries (import as plain transactions, but flag them so the synthetic adjustment doesn't double-count).

### 16. Config file must support multiple import pairs
One LM account will receive transactions from multiple YNAB budgets (e.g. CAD + BRL). The config schema must enumerate `(ynab_budget_id, lm_account_id-or-mapping-rules, per-pair-knob-overrides)` tuples, not a single global set of values. Each pair owns its own opening-balance category, uncategorised fallback, deferred-income policy, account-exclusion list, and zero-based mode flag. Cross-pair invariants (e.g. don't double-import a transaction that appears in two exports) need their own section.

### 17. Migration plan still reflects the old "match existing LM entities" strategy
[migration-plan.md](migration-plan.md) describes Phase 0 as matching YNAB accounts/categories against existing LM ones by name or `external_id` (Accounts step 2; Categories step 2). The current strategy is **create everything from scratch in LM** — the importer owns its target accounts and categories and does not try to merge with pre-existing user-created entities. The migration plan needs to be rewritten to reflect this; the transaction-import plan inherits the consequences:
  - Phase 0 Accounts: drop "match against existing LM manual accounts". Always create. The only matching step that remains is the Plaid-linked account special case (step 3), which exists because Plaid accounts can't be created by the importer.
  - Phase 0 Categories: drop "match against existing LM categories". Always create. Category mapping is sync-state-driven only.
  - Phase 1 Transactions: account/category lookups never fall back to "find by name in LM" — every reference must come from sync_state, and a missing entry is a hard error (see [[project_transaction_import_deps]]).
  - Re-runs: the create-from-scratch invariant only holds on the initial import; subsequent runs use sync_state for everything. Document this clearly so users don't expect the importer to reconcile against LM changes they made themselves (related to item 13).
  - Config implication: the per-pair config (item 16) should specify the *target LM account* (which will be created if absent) and the *category-group naming convention* (e.g. prefix categories with the budget name to disambiguate multi-budget imports into a single LM account). These can no longer rely on "the user has already set up the matching entities in LM".
