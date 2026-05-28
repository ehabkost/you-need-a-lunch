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
