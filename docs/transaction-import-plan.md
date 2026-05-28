# Transaction Import — Internal Category Handling Plan

## Pre-classification

For each YNAB transaction, compute these flags first:

```python
is_transfer         = txn.transfer_account_id is not None
is_starting_balance = txn.payee_name == "Starting Balance"
is_zero             = txn.amount == 0
cat_name            = txn.category_name  # may be None
```

## Decision table

| # | Case | Action | LM `category_id` | Notes |
|---|------|--------|------------------|-------|
| 1 | `Inflow: Ready to Assign` + transfer (inflow side) | **Import** as transfer leg | LM "Payment, Transfer" | Both legs of a transfer must be imported; see Transfer Strategy |
| 2 | `Inflow: Ready to Assign` + non-transfer income | **Import** | Mapped LM income category (`is_income=True`); looked up during Phase 0 | Canonical income mapping |
| 3 | `Inflow: Ready to Assign` + Starting Balance + zero | **Skip** | — | Pure YNAB metadata |
| 4 | `Inflow: Ready to Assign` + Starting Balance + non-zero | **Import as opening balance** | null (or `--opening-balance-category`) | Set `custom_metadata.ynab_starting_balance=true`; payee = "Starting Balance" |
| 5 | `Uncategorized` + transfer (outflow side) | **Import** as transfer leg | LM "Payment, Transfer" | Both legs of a transfer must be imported; see Transfer Strategy |
| 6 | `Uncategorized` + regular spending | **Import** | null | Tag `custom_metadata.ynab_uncategorized=true` so user can filter & clean up post-import |
| 7 | `Uncategorized` + Starting Balance + zero | **Skip** | — | Pure metadata |
| 8 | `Uncategorized` + Starting Balance + non-zero | **Import as opening balance** | null | Same as case 4; negative amount handles liability accounts |
| 9 | `Deferred Income SubCategory` on any txn | **Warn + require user decision** | — | Per CLAUDE.md: offer choices — (a) treat as income, (b) treat as uncategorized, (c) skip. Cache decision in sync_state |
| 10 | `Credit Card Payments` group categories | **Abort (hard error)** | — | Pre-flight scan must detect these and abort before any writes; should never occur in practice |

## Transfer Strategy (cases 1 & 5)

LM has no native transfer pairing. The recommended LM approach (per LM support docs) is to import **both legs** as separate transactions, both categorized as the default **"Payment, Transfer"** category (exclude-from-budget + exclude-from-totals). The Transfer Management Tool (see `future-tools.md`) can then optionally group the two legs.

- Import **both** the outflow leg (case 5) and the inflow leg (case 1).
- Category for both legs = LM "Payment, Transfer" category. Look up its ID once during Phase 0; create it if missing (with `exclude_from_budget=true`, `exclude_from_totals=true`).
- Store `ynab_paired_id` on both imported transactions so the Transfer Management Tool can match and group them later.
- If one account was **not migrated** (excluded or Plaid read-only): import only the migrated leg as a "Payment, Transfer" transaction. The other leg is skipped; no grouping possible.
- **Do not use LM's "create transfer" UI action** on already-imported transactions — it creates a spurious third entry.

### Cross-currency transfers

When a transfer is between accounts of different currencies (e.g. CAD checking → BRL account within the same budget), YNAB links them with `transfer_account_id` but records each in its own currency. LM handles this the same way: two separate "Payment, Transfer" transactions in their respective account currencies, left **ungrouped** (grouping cross-currency transactions in LM produces a non-zero total due to FX rate differences). Store `ynab_paired_id` on both for reference.

## Opening Balance Handling (cases 4 & 8)

- Import as a one-sided transaction in LM with the YNAB `date` and amount.
- If `--since` cutoff is later than the Starting Balance date: **still import** (date-filter exception) so reconciliation works. See [balance-reconciliation.md](balance-reconciliation.md).
- Payee = "Starting Balance"; LM category = null by default.
- Shown as a separate count in the dry-run summary: `opening_balances: N`.

## Dry-Run Summary Buckets

```
transactions:
  income (Inflow: Ready to Assign):            N
  uncategorized (regular):                     N
  transfers (both legs migrated):              N
  transfers (one leg only, other excluded):    N
  opening balances:                            N
  skipped (zero-amount starting bal):          N
  skipped (deleted):                           N
  needs user decision (Deferred Inc.):         N
  ABORT — unexpected CC Payment category:      N  ← hard stop if > 0
```

## Edge Cases

1. **Negative income** (refund on `Inflow: Ready to Assign`, non-transfer): import as income with negative amount; LM accepts this. Do not flip sign.
2. **Transfer to a deleted account**: import only the surviving-account leg as a "Payment, Transfer" transaction.
3. **"Uncategorized" on inflow (positive amount, no transfer)**: rare (income never categorized in YNAB). Treat as case 6 (null category + flagged), **not** as income — preserves user intent.
4. **Re-run safety**: every imported txn must carry `custom_metadata.ynab_id`; transfer pairs also carry `ynab_paired_id`. Lookup before insert; rely on LM's `skipped_duplicates` as a second line of defense.
5. **Balance adjustments** (`payee='Manual Balance Adjustment'` or `'Reconciliation Balance Adjustment'`): import as plain transaction with null category, preserve payee text. Flag in dry-run summary.

## Config Knobs

- `--opening-balance-category <category_id>`: override cases 4/8 to use a specific category instead of null.
- `--deferred-income-as {income,uncategorized,skip}`: pre-answer case 9 non-interactively.

---

## Credit Card Transactions

YNAB's CC budgeting model (auto-generated "Credit Card Payment" categories, budget-side fund shifts) is a pure budgeting abstraction with no transaction-level footprint. CC accounts behave like any other liability account for import — no special-casing required at the transaction level.

### Decision table

| YNAB transaction shape | LM treatment |
|---|---|
| Expense on CC account with real category (Groceries, Uber Eats, etc.) | Import as normal expense on the LM CC (liability) account. Map category via sync_state. No special handling. |
| CC payment: transfer checking → CC, `category='Uncategorized'`, `payee='Transfer : ...'` | Import **both** legs as "Payment, Transfer" per the transfer strategy above. |
| `cat='Inflow: Ready to Assign'`, `amount=0`, `payee='Starting Balance'` on CC | Skip (existing rule). |
| `cat='Inflow: Ready to Assign'`, `amount≠0`, `payee='Starting Balance'` on CC | Import as opening balance on the LM CC account (existing rule). |
| `cat='Inflow: Ready to Assign'`, `amount≠0`, `payee='Manual Balance Adjustment'` or `'Reconciliation Balance Adjustment'` on CC | Import as plain transaction, null category, preserve payee text. Flag in dry-run summary. |
| Refund/return on CC (positive-amount expense with real category) | Import as-is — LM handles positive amounts on liability accounts fine. |

### "Credit Card Payment" budget categories

The entire "Credit Card Payments" group is skipped in Phase 0 (correct). Pre-flight must **abort** if any transaction carries a `Credit Card Payments` group category — this should never occur, and if it does it indicates an unexpected YNAB data shape that must be resolved manually before import.

### Other CC edge cases

- **Foreign-currency CCs**: the account-exclusion rules (multi-budget.md) handle this at the account level. Transfer-skip logic must check exclusion on *both* legs.
- **CC interest charges**: usually manual expenses with a real category. No special handling; flag in dry-run so user can verify reconciliation.

---

## Open Questions

### 1. "Deferred Income" semantics are undefined
Case 9 defers to the user, but the doc never defines what YNAB's "Deferred Income SubCategory" represents, when YNAB auto-creates it, or whether it can carry a non-zero balance across the cutoff date. Research the YNAB behaviour and document a canonical mapping recommendation before asking the user to choose.

### 2. In-budget vs. off-budget account handling is unspecified
YNAB distinguishes on-budget from off-budget ("Tracking") accounts. Transactions on tracking accounts don't affect category balances. The LM equivalent (excluding accounts from budget calculations) needs to be set per account, and transactions on tracking accounts must not be assigned categories that would distort LM's category totals. The plan currently treats all accounts identically.

### 3. No method for verifying category balances match post-import
Account balance reconciliation is documented (balance-reconciliation.md) but there's no equivalent for category balances. Need: a pass that sums imported transactions per LM category and compares against YNAB's category activity for the same date range, plus a documented tolerance.

### 4. Transfer-leg metadata rules are missing
The pairing strategy covers `ynab_id` / `ynab_paired_id` but is silent on per-leg metadata: memo/notes, flags, cleared status, approved status, original payee strings. Both YNAB legs can carry different values; need explicit rules (e.g. preserve each leg's own memo; store both flag colors in `custom_metadata`).

### 5. Category balance reconstruction under partial import (`--since`)
Account balances are repaired via opening-balance transactions, but categories have no "opening balance" concept. When history before the cutoff is excluded, category balances cannot be reconstructed the same way.

### 6. Late-arriving (earlier) transactions break opening balances
If a user runs `--since 1y` and later backfills older history, the synthesised opening-balance transactions will double-count. Need: a documented backfill procedure, a `sync_state` field tracking the current cutoff date, and detection logic that adjusts or removes opening-balance entries when an earlier cutoff is requested.

### 7. Debt accounts have unreconcilable balances without a synthetic interest entry
Per [mortgage-debt-tracking.md](mortgage-debt-tracking.md), YNAB debt accounts embed accrued interest into the account `balance` with no corresponding transactions. After importing transactions, a synthetic "Accrued Interest" adjustment may be needed. See the debt-tracking doc for details.

### 8. Re-sync after the user has been actively using LM (deferred)
If the user merges LM accounts, moves transactions, or splits/merges categories after the initial import, sync_state goes stale and re-runs will misbehave. **Not supported in v1.** Document known failure modes for future implementers.

### 9. Config file for per-import-pair settings
Options like `--opening-balance-category` and `--deferred-income-as` are per-import-pair settings that belong in a version-controllable config file rather than CLI flags.

### 10. LM account-merging behaviour is unverified
The post-import workflow in [multi-currency-strategy.md](multi-currency-strategy.md) assumes the user can merge/restructure LM accounts. Verify: Does LM support merging manual accounts? What happens to `external_id`, `custom_metadata`, and transaction history? Does the surviving account's ID stay stable?

### 11. LM multi-currency category-balance semantics are unverified
When transactions in two currencies share an LM category, does LM compute a per-currency total, a base-currency total via FX, or both? Tracked in [multi-currency-strategy.md](multi-currency-strategy.md).
