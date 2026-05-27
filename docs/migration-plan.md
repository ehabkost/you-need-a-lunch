# Migration Plan: YNAB → Lunch Money

## Overview

The migration is structured in phases, with each phase building on previous work. Importers must always show a dry-run summary before applying changes.

## Phase 0: Accounts (v0.1)

**Prerequisite**: if any YNAB accounts have `direct_import_linked: true`, the user must have already set up Plaid bank sync for those accounts in Lunch Money before running the importer. The importer will warn and require confirmation if it cannot find a Plaid account match for a `direct_import_linked` YNAB account.

### Steps

1. Export all YNAB accounts
2. Match against existing Lunch Money manual accounts (by name, or by `external_id` if re-running)
3. For YNAB accounts with `direct_import_linked: true`, match to Lunch Money Plaid accounts by name (case-insensitive) — these must NOT be re-created as manual accounts; transactions into them should target the matched Plaid account if `allow_transaction_modification: true`, or be skipped if `allow_transaction_modification: false`
   - If no Plaid match is found: warn the user and skip the account (do not create a manual account). The user must resolve this manually — either by connecting Plaid sync in LM or by explicitly opting in to creating a manual account for it
4. For closed YNAB accounts (`closed: true`): create as manual accounts in Lunch Money (so their transaction history is preserved), but note they are closed in `custom_metadata`
5. Create missing manual accounts in Lunch Money, storing YNAB UUID in `external_id`
6. **Balance check**: after all transactions are imported, compare computed balance against YNAB's `cleared_balance` and `uncleared_balance`

## Phase 0: Categories (v0.2)

1. Export YNAB category groups and categories
2. Match against existing Lunch Money categories (by name, or stored metadata)
3. Create missing categories, preserving group structure
4. Store YNAB category UUID mapping for use during transaction import

## Phase 1: Transactions

1. Export all YNAB transactions (excluding deleted)
2. For each transaction:
   - Skip if it belongs to a Plaid-linked account in Lunch Money
   - Skip transfers (where `transfer_account_id` is set) — transfers will be handled as a pair to avoid double-counting
   - Convert amount: YNAB milliunits ÷ 1000, negate sign (YNAB negative → LM positive)
   - Map `payee_name`, `category_id` (via stored mapping), `account_id`
   - Store YNAB `id` in transaction `custom_metadata` as `{"ynab_id": "..."}`
   - For split transactions (has `subtransactions`): create as split in Lunch Money
3. Show summary before applying: count of new/skipped/conflicting transactions
4. Send transactions to `POST /v2/transactions` in batches of **500** (the documented maximum per request)
5. For each batch response, process `skipped_duplicates`:
   - Each entry contains `reason`, `request_transactions_index`, `existing_transaction_id`, and `request_transaction`
   - Log each skipped transaction with its reason; count toward the pre-apply summary
   - Partial insertion is safe: non-duplicates in the same batch are inserted even when some are skipped

### Transfer handling

YNAB creates two linked transactions for every transfer (one per account). Lunch Money may handle transfers differently. Strategy TBD — options:
- Import only one leg of each transfer pair and mark the other as skipped
- Use Lunch Money's transfer concept if available in v2

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
