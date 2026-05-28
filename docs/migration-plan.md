# Migration Plan: YNAB → Lunch Money

## Overview

The migration is structured in phases, with each phase building on previous work. Importers must always show a dry-run summary before applying changes.

Crash resistance: every LM entity created during import gets `custom_metadata.ynab_id` (or `external_id` for accounts) set immediately. `sync_state.json` is saved after each individual write. A re-run after a crash will recover already-created entities from LM metadata rather than duplicating them.

## Phase 0: Accounts (v0.1)

**Prerequisite**: if any YNAB accounts have `direct_import_linked: true`, the user must have already set up Plaid bank sync for those accounts in Lunch Money before running the importer. The importer will warn and require confirmation if it cannot find a Plaid account match for a `direct_import_linked` YNAB account.

### Steps

1. Export all YNAB accounts
2. For each account (skipping deleted):
   - Check `sync_state.json` → already synced, skip
   - Check LM manual accounts for `external_id == ynab:{budget_id}:{account_id}` → crash recovery: record in sync_state
   - For `direct_import_linked: true`: match to LM Plaid account by name. If `allow_transaction_modification: true` → record as plaid match. If `false` → skip (read-only). If no Plaid match → warn and skip
   - Otherwise: create LM manual account with `external_id = ynab:{budget_id}:{account_id}` and `custom_metadata` carrying YNAB type/flags; save sync_state immediately
3. For closed YNAB accounts (`closed: true`): create as manual accounts to preserve transaction history; set `exclude_from_transactions: true`
4. **Balance check**: after all transactions are imported, compare computed balance against YNAB's `cleared_balance` and `uncleared_balance`

## Phase 0: Categories (v0.2)

All YNAB categories are imported directly — no manual mapping step required.

### Steps

1. Export all YNAB category groups and categories
2. Skip YNAB system groups: `"Internal Master Category"`, `"Credit Card Payments"`, `"Hidden Categories"`
3. For each non-system group (skipping deleted):
   - Check `sync_state.json` → already synced, skip
   - Check LM categories for `custom_metadata.ynab_id` match → crash recovery: record in sync_state
   - Otherwise: create LM category group with `custom_metadata.ynab_id`; save sync_state immediately
4. For each non-internal category within non-system groups (skipping deleted):
   - Same three-step check: sync_state → LM metadata match → create
   - `hidden: true` categories → create with `archived: true` in LM
   - `custom_metadata.ynab_id` set on every created category
5. `"Inflow: Ready to Assign"` (internal): map to the first LM `is_income: true` category found; record in sync_state for use during transaction import

## Phase 1: Transactions

See **[transaction-import-plan.md](transaction-import-plan.md)** for the full decision table.

### Pre-classification flags

```python
is_transfer        = txn.transfer_account_id is not None
is_starting_balance = txn.payee_name == "Starting Balance"
is_zero            = txn.amount == 0
```

### Internal category handling

| Category | Situation | Action |
|---|---|---|
| `Inflow: Ready to Assign` | Transfer inflow side | Skip; record `ynab_id` in sync_state for dedup |
| `Inflow: Ready to Assign` | Non-transfer income | Import with mapped LM income category |
| `Inflow: Ready to Assign` | Starting Balance, zero | Skip |
| `Inflow: Ready to Assign` | Starting Balance, non-zero | Import as opening balance, null category |
| `Uncategorized` | Transfer outflow side | Import as transfer, null category |
| `Uncategorized` | Regular spending | Import, null category, flag `ynab_uncategorized=true` |
| `Uncategorized` | Starting Balance, zero | Skip |
| `Uncategorized` | Starting Balance, non-zero | Import as opening balance, null category |
| `Deferred Income SubCategory` | Any | Warn + require user decision (abort unless resolved in sync_state) |
| `Credit Card Payments` group | Any | Warn + treat as uncategorized (should not occur in practice) |

### Transfer strategy

YNAB creates two paired transactions per transfer; LM uses one-sided entries.

- Process the **outflow side only** (negative amount, source account). Skip the inflow side but record both `ynab_id`s in `custom_metadata` (`ynab_id` + `ynab_paired_id`) for dedup on re-runs.
- If the destination account was not migrated (excluded or Plaid read-only): import the outflow as a regular transaction with null category and original payee text.
- Cross-currency transfers (e.g. BRL ↔ CAD): YNAB stores two independent transactions with different amounts. Import as two unpaired regular transactions with `custom_metadata.ynab_cross_currency_transfer=true`.

### Credit card transactions

No special handling required at the transaction level. CC spending is recorded on the CC account with real spending categories, same as any other account. CC payments are transfers (covered above). The "Credit Card Payment" budget categories are a YNAB budgeting abstraction with no transaction footprint — they are skipped in Phase 0 and never appear on transactions.

### Opening balances

- Zero-amount starting balance entries → skip
- Non-zero starting balance entries → import, ignoring `--since` cutoff (needed for balance reconciliation). Payee = "Starting Balance", null category, `custom_metadata.ynab_starting_balance=true`.

### Steps

1. Export all YNAB transactions (excluding deleted)
2. Classify each transaction using the rules above
3. Show dry-run summary by bucket before applying:
   - income / uncategorized / transfers (paired) / transfers (one-sided) / opening balances / skipped / needs-user-decision
4. Abort if any "needs user decision" bucket is non-zero and no resolution is cached in sync_state
5. Convert amounts: YNAB milliunits ÷ 1000, negate sign (YNAB negative = outflow → LM positive expense)
6. Store YNAB `id` in `custom_metadata.ynab_id` on every imported transaction
7. Send to `POST /v2/transactions` in batches of **500**
8. Process `skipped_duplicates` in each response; log with reason and count

## Phase 2: Budget Assignments

- Lunch Money has a budget endpoint; confirm if it supports setting budgeted amounts per category per month
- Import YNAB monthly `budgeted` values per category
- Convert milliunits to decimal

## Phase 3: Budget Goals

- Embedded in YNAB categories; check if Lunch Money supports goals on categories
- If not supported, export to a reference file for manual recreation

## Phase 4: Scheduled Transactions

- Export YNAB scheduled transactions
- Map to Lunch Money recurring items (if the API supports creation)
- Map YNAB frequency enum to Lunch Money's recurring cadence
