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
| 10 | `Credit Card Payments` group categories on a transaction | **N/A — does not occur** | — | Confirmed: zero transactions reference these categories. The group is imported as a normal (empty) group; no transaction is ever assigned to it. See [credit-cards.md](credit-cards.md). |

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
```

(No CC-Payment-category bucket: those categories are created empty in Phase 0 and
never receive transactions. See [credit-cards.md](credit-cards.md).)

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

See **[credit-cards.md](credit-cards.md)** for the full treatment.

Summary: YNAB's CC budgeting model (auto-generated "Credit Card Payment"
categories, budget-side fund shifts) is a pure budgeting abstraction with **no
transaction-level footprint**. CC accounts import as ordinary liability accounts
and need **no special-casing at the transaction level**:

- CC purchases → normal expenses on the LM CC (Credit) account, real category
  mapped via sync_state.
- CC payments (checking → CC) → both legs as "Payment, Transfer" (the
  [Transfer Strategy](#transfer-strategy-cases-1--5) above).
- Opening balances, manual/reconciliation adjustments, refunds, interest → handled
  by the existing decision-table and edge-case rules.

The **"Credit Card Payments" category group is imported as a normal group** in
Phase 0. Its per-card categories (e.g. `Edu Credit CIBC Visa 💳`,
`Costco Mastercard 🛒`) are created in LM but stay **empty** — confirmed: zero
transactions reference them. They are harmless; the user may archive or delete
them after import. There is **no pre-flight abort** for these categories (the
shape that abort guarded against does not occur).

Note: the group is flagged `internal: true` in the YNAB export, but its member
categories are not — so Phase 0's group-skip rule (skip only if no importable
categories) passes it through without any special-casing. See
[credit-cards.md](credit-cards.md).

---

## Tracking ("Off-Budget") Account Handling

YNAB distinguishes **on-budget** from **off-budget** ("Tracking") accounts. Transactions on tracking accounts don't affect category balances; they affect net worth only.

**LM model** (per [Category Properties](https://support.lunchmoney.app/setup/categories/category-properties) and [Managing Accounts](https://support.lunchmoney.app/setup/accounts/managing-accounts)):
- LM has no per-account "exclude from budget" toggle. Budget exclusion is **category-driven** via the `Exclude from budget` and `Exclude from totals` category properties.
- LM accounts do expose a `Do not track transactions` toggle, but it disables manual transactions entirely — not suitable for tracking-account history that already has transactions.

**Decision**: For YNAB tracking accounts, import transactions but map them to a dedicated **"Tracking (off-budget)"** category with both `exclude_from_budget=true` and `exclude_from_totals=true`. Create this category once in Phase 0 if missing. Look up its ID and use it for any transaction whose source YNAB account is `on_budget=false`, overriding the category resolution from the decision table above (except transfers — transfers always use "Payment, Transfer").

**Dry-run**: count these as a separate bucket `tracking (off-budget): N` so the user sees their volume.

## Multi-Currency Category Totals

Confirmed via LM docs ([Multicurrency](https://support.lunchmoney.app/settings/multicurrency)): LM converts all amounts to the **primary currency** using **historical FX rates** (rate of the transaction date) for category totals, budgets, and summaries. There is no per-currency category breakdown.

**Implication**: When the same LM category receives transactions in multiple currencies (e.g. groceries in CAD and BRL), category-activity totals will differ from any per-currency YNAB total by the FX conversion. This is expected, not a bug. Category-balance verification (below) must convert YNAB activity through the same historical rates before comparing, or compare per-currency-per-category.

## Category-Balance Verification

LM has no public API for "category balance over period"; balances live on the budget endpoints. The reconciliation strategy:

1. After import, for each (LM category, period) pair within the imported range, sum the imported transactions (converting to primary currency via historical rate if multi-currency).
2. Compare against YNAB's per-category activity for the same period (from `categories` snapshots).
3. Tolerance: zero for same-currency single-currency budgets; **±0.5%** per category-month for multi-currency (FX-rate-source differences).
4. Output: a `category-recon.md` report flagging deltas above tolerance. Not a hard gate — informational.

## Account Merging in LM

Confirmed via [Managing Accounts](https://support.lunchmoney.app/setup/accounts/managing-accounts):
- LM supports merging both manually-managed and synced accounts. Transactions, recurring items, and rules migrate to the destination account. User chooses which account's balance history to keep.
- The merge is irreversible; the source account ID is destroyed and the destination ID stays stable.
- LM docs don't specify the fate of `external_id` / `custom_metadata` on a merge. **Implication for re-sync**: after a user merges accounts post-import, the source account's `external_id` (`ynab:{budget}:{account}`) is gone, so the next sync run can't find it. This is one of the v1 known failure modes (see Open Question 5 below).

## Open Questions

### 1. "Deferred Income" semantics are undefined
Case 9 defers to the user, but the doc never defines what YNAB's "Deferred Income SubCategory" represents, when YNAB auto-creates it, or whether it can carry a non-zero balance across the cutoff date. Research the YNAB behaviour and document a canonical mapping recommendation before asking the user to choose.

### 2. Transfer-leg metadata rules are missing
The pairing strategy covers `ynab_id` / `ynab_paired_id` but is silent on per-leg metadata: memo/notes, flags, cleared status, approved status, original payee strings. Both YNAB legs can carry different values; need explicit rules (e.g. preserve each leg's own memo; store both flag colors in `custom_metadata`).

### 3. Category balance reconstruction under partial import (`--since`)
Account balances are repaired via opening-balance transactions, but categories have no "opening balance" concept (LM categories don't support synthetic opening entries the way accounts do). When history before the cutoff is excluded, category balances cannot be reconstructed the same way. Options to consider: (a) accept that pre-cutoff activity is invisible to category reports; (b) synthesise a single excluded-from-totals "pre-cutoff catch-up" transaction per category; (c) document that category-balance verification is only meaningful for periods entirely inside the import window.

### 4. Late-arriving (earlier) transactions break opening balances
If a user runs `--since 1y` and later backfills older history, the synthesised opening-balance transactions will double-count. Need: a documented backfill procedure, a `sync_state` field tracking the current cutoff date, and detection logic that adjusts or removes opening-balance entries when an earlier cutoff is requested.

### 5. Debt accounts have unreconcilable balances without a synthetic interest entry
Per [mortgage-debt-tracking.md](mortgage-debt-tracking.md), YNAB debt accounts embed accrued interest into the account `balance` with no corresponding transactions. After importing transactions, a synthetic "Accrued Interest" adjustment may be needed. See the debt-tracking doc for details.

### 6. Re-sync after the user has been actively using LM (deferred)
If the user merges LM accounts (see "Account Merging in LM" above — `external_id` survival is unverified), moves transactions, or splits/merges categories after the initial import, sync_state goes stale and re-runs will misbehave. **Not supported in v1.** Document known failure modes for future implementers.

### 7. Config file for per-import-pair settings
Options like `--opening-balance-category` and `--deferred-income-as` are per-import-pair settings that belong in a version-controllable config file rather than CLI flags.

### 8. `external_id` / `custom_metadata` survival across LM account merge
LM docs confirm merge migrates transactions, recurring items, and rules — but don't say whether the destination account inherits the source's `external_id` or `custom_metadata`, nor whether transaction-level `custom_metadata.ynab_id` survives. Needs API-level verification (test merge of two manual accounts with metadata set). Drives whether re-sync can detect a post-merge account via stable IDs.
