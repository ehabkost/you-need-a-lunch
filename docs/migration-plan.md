# Migration Plan: YNAB → Lunch Money

> **For current status, priority, and sequencing of every phase, see [ROADMAP.md](ROADMAP.md)**
> (the authority for what is done / next / blocked / deferred). This doc describes the *design*
> of each phase.

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
5. `"Inflow: Ready to Assign"` (internal): map to the first LM `is_income: true` category found; record in sync_state for use during transaction import. Also record its YNAB UUID under `sync_state.ynab_internal_cats["inflow"]` so Phase 1 can recognise the inflow category by id (the `"uncategorized"` UUID is already recorded the same way).
6. **Special LM-native categories** (no YNAB equivalent) needed by Phase 1, each created once (or recovered by name) with `exclude_from_budget=true` + `exclude_from_totals=true`, and recorded under `sync_state.special_categories`:
   - `payment_transfer` → "Payment, Transfer" (both transfer legs)
   - `tracking_off_budget` → "Tracking (off-budget)" (off-budget account transactions)
   - `incomplete_split` → "Incomplete Split" (holding category for native split parents between the two import passes — see [transaction-importer-implementation.md §4](transaction-importer-implementation.md))

## Phase 1: Transactions

See **[transaction-import-plan.md](transaction-import-plan.md)** for the full decision table,
and **[transaction-importer-implementation.md](transaction-importer-implementation.md)** for the
code architecture (pure classify+convert core, the API/directory sink split, and the
directory-output mode that lets the importer be unit-tested without the LM API).

### Pre-classification flags

```python
is_transfer        = txn.transfer_account_id is not None
is_starting_balance = txn.payee_name == "Starting Balance"
is_zero            = txn.amount == 0
```

### Internal category handling

| Category | Situation | Action |
|---|---|---|
| `Inflow: Ready to Assign` | Transfer inflow side | Import as "Payment, Transfer" (both transfer legs must be imported) |
| `Inflow: Ready to Assign` | Non-transfer income | Import with mapped LM income category |
| `Inflow: Ready to Assign` | Starting Balance, zero | Skip |
| `Inflow: Ready to Assign` | Starting Balance, non-zero | Import as opening balance, null category |
| `Uncategorized` | Transfer outflow side | Import as "Payment, Transfer" (both transfer legs must be imported) |
| `Uncategorized` | Regular spending | Import, null category, flag `ynab_uncategorized=true` |
| `Uncategorized` | Starting Balance, zero | Skip |
| `Uncategorized` | Starting Balance, non-zero | Import as opening balance, null category |
| `Deferred Income SubCategory` | Any | Warn + require user decision (abort unless resolved in sync_state) |
| `Credit Card Payments` group | Any | **N/A — does not occur.** Confirmed zero transactions reference these categories; no pre-flight abort. See [credit-cards.md](credit-cards.md). |

### Transfer strategy

LM has no native transfer pairing. The recommended approach (per LM support docs) is to import **both legs** as separate transactions, both categorized as LM's default **"Payment, Transfer"** category (exclude-from-budget + exclude-from-totals). The Transfer Management Tool (see `future-tools.md`) can later group them.

- Import **both** the outflow and inflow legs. Category for both = "Payment, Transfer".
- Look up the LM "Payment, Transfer" category ID once during Phase 0; create it if missing.
- Store `ynab_paired_id` on both transactions so the Transfer Management Tool can match and group them.
- If one account was not migrated (excluded or Plaid read-only): import only the migrated leg as "Payment, Transfer"; skip the other.
- Cross-currency transfers (e.g. CAD checking → BRL account within the same budget): import both legs in their respective account currencies. Leave them **ungrouped** — LM does not produce a meaningful zero total when grouping cross-currency transactions.

### Credit card transactions

No special handling required at the transaction level. CC spending is recorded on the CC account with real spending categories, same as any other account. CC payments are transfers — both the checking debit and the CC credit are imported as "Payment, Transfer" per the transfer strategy above. The "Credit Card Payments" group is imported as a normal (empty) group in Phase 0; its per-card categories receive **zero** transactions (confirmed in the data), so there is **no** pre-flight abort. See [credit-cards.md](credit-cards.md).

### Opening balances

- Zero-amount starting balance entries → skip
- Non-zero starting balance entries → import, ignoring `--since` cutoff (needed for balance reconciliation). Payee = "Starting Balance", null category, `custom_metadata.ynab_starting_balance=true`.

### Steps

1. Export all YNAB transactions (excluding deleted)
2. Classify each transaction using the rules above
3. Show dry-run summary by bucket before applying:
   - income / uncategorized / transfers (paired) / transfers (one-sided) / opening balances / skipped / needs-user-decision
4. Abort if any "needs user decision" bucket is non-zero and no resolution is cached in sync_state
5. Convert amounts: **negate** then ÷1000 to a 4-dp string (YNAB negative outflow → LM positive debit). Use the single helper in [amount-conversion.md](amount-conversion.md).
6. Store YNAB `id` in `custom_metadata.ynab_id` on every imported transaction
7. Send to `POST /v2/transactions` in batches of **500**
8. Process `skipped_duplicates` in each response; log with reason and count
9. **Split transactions** are imported natively in two passes (parent insert, then `POST /transactions/split/{id}`) so each parent keeps the real charge amount for statement reconciliation. See [transaction-importer-implementation.md §4](transaction-importer-implementation.md) for the full two-pass flow, the "Incomplete Split" holding category, and the `sync_state` split tracking.

## Phase 2: Budget Assignments

- Lunch Money has a budget endpoint; confirm if it supports setting budgeted amounts per category per month
- Import YNAB monthly `budgeted` values per category
- Convert milliunits to decimal

## Phase 3: Budget Goals

- Embedded in YNAB categories; check if Lunch Money supports goals on categories
- If not supported, export to a reference file for manual recreation

## Phase 4: Scheduled Transactions

See **[scheduled-transactions-import-plan.md](scheduled-transactions-import-plan.md)** for the
detailed plan. Summary:

- Export YNAB scheduled transactions (already done — `data/<slug>/scheduled_transactions.json`)
- **Blocking constraint:** LM v2 API has **no create endpoint** for recurring items (only `GET`).
  The phase therefore emits a manual-entry **worklist** + relies on LM auto-detected suggestions,
  rather than pushing via API.
- Map YNAB frequency enum to LM's `granularity` × `quantity` cadence (see the plan's §4 table)
