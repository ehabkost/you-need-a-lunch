# Credit Card Handling

How the importer treats credit-card (CC) accounts, CC spending, CC payments, and
YNAB's auto-generated "Credit Card Payment" budget categories.

Audience: the developer implementing Phase 1 (transaction import). This document
is the authoritative reference for CC behaviour; the CC section of
[transaction-import-plan.md](transaction-import-plan.md) defers to it.

## TL;DR

- YNAB's CC budgeting model is a **pure budgeting abstraction**. It has **no
  transaction-level footprint**, so the importer needs **no special-casing at the
  transaction level** for credit cards.
- A YNAB CC account is just a liability account. It imports like any other
  liability account.
- CC purchases import as normal expenses on the LM CC account, with their real
  category mapped via sync_state.
- CC payments (checking → CC) are transfers and import as **both legs** using the
  LM **"Payment, Transfer"** category — same as every other transfer.
- The "Credit Card Payments" category group is now imported as a normal group.
  Its categories will be **created in LM but will always be empty** (zero
  transactions reference them). They are harmless; the user may archive or delete
  them post-import.

## YNAB's credit card model (what matters for import)

YNAB treats a credit card as an **account** (a liability) and layers a budgeting
workflow on top of it:

- When you create an on-budget credit card account, YNAB **auto-generates a
  budget category** named after the card, inside a system group called
  **"Credit Card Payments"**. In our export data these look like
  `Edu Credit CIBC Visa 💳`, `Costco Mastercard 🛒`, etc. — one per on-budget
  card, named to match the account.
- The intended workflow: when you spend on the card from a normal budget category
  (e.g. Groceries), YNAB **moves the budgeted money** from that spending category
  into the card's Credit Card Payment category. The Payment category's "Available"
  amount then represents money set aside to pay the card. This is YNAB's way of
  reminding you that spending on credit creates a future payment obligation.
- Crucially, **all of that is budget-side bookkeeping**. The fund shifts between
  categories are recorded in YNAB's *budget/month* data (category `budgeted`/
  `activity`/`balance` per month), **not** as transactions. No transaction ever
  carries a Credit Card Payment category in normal use.
- The actual purchase is an ordinary transaction on the card account with a
  **real** category (Groceries, Restaurants, …).
- The actual card payment is an ordinary **transfer** transaction: an outflow
  from the funding account (checking) and an inflow to the card account. In YNAB
  these legs carry `transfer_account_id` and a payee like `Transfer : <account>`,
  with category `Uncategorized` (YNAB does not categorize transfer legs).

### Confirmed from our export data

A scan of the YNAB export (`data/cad`, `data/brl`, `data/usd`) confirms:

- **Zero non-transfer transactions reference any "Credit Card Payments" group
  category.** Every transaction that touches a card is either a real-category
  purchase or a transfer leg. This is what makes the CC budgeting model safe to
  ignore at the transaction level.

## How Lunch Money handles credit cards

LM has no YNAB-style CC budgeting concept. A credit card is simply an account of
type **Credit** (a liability):

- Account types are split by net-worth treatment
  ([Accounts FAQ](https://support.lunchmoney.app/setup/accounts/faq)): for Credit
  and Loan accounts a **positive balance denotes amount owed** and is **deducted**
  from net worth. (For asset types a positive balance adds to net worth.) So the
  importer must set the CC account's LM type to `credit` and keep YNAB's balance
  sign convention consistent (see account-type mapping work and
  [balance-reconciliation.md](balance-reconciliation.md)).
- A card payment in LM is modelled exactly like any internal transfer
  ([Transactions FAQ](https://support.lunchmoney.app/finances/transactions/transactions),
  [Migrating from YNAB](https://support.lunchmoney.app/guides/migrating-from-ynab)):
  a debit on the cash account and a credit on the card account, **both
  categorized "Payment, Transfer"**. That category is excluded from totals and
  from budget, so payments don't distort spending/income or budgets. The two
  legs net to $0 and may optionally be grouped.
- LM purposely uses "exclude from totals" on transfer-type categories so that
  large CC bill payments don't pollute Trends/Stats
  ([Trends](https://support.lunchmoney.app/home/trends),
  [Stats](https://support.lunchmoney.app/home/stats)).

The net effect: LM's CC model is just "liability account + transfer category."
There is nothing in LM that corresponds to YNAB's Credit Card Payment categories,
which is exactly why importing those categories produces empty-but-harmless
categories rather than anything meaningful.

## Import strategy per transaction shape

All of these follow the general rules already in
[transaction-import-plan.md](transaction-import-plan.md); none is special-cased
*because* it is a credit card.

| YNAB transaction shape (on a CC account) | LM treatment |
|---|---|
| Purchase with a real category (Groceries, Uber Eats, …) | Import as a normal expense on the LM CC (Credit) account. Map category via sync_state. No special handling. |
| Refund / return (positive-amount purchase with a real category) | Import as-is. LM accepts positive amounts on liability accounts; do not flip the sign. |
| CC payment: transfer checking → CC (`transfer_account_id` set, `category='Uncategorized'`, payee `Transfer : …`) | Import **both** legs as "Payment, Transfer" per the [Transfer Strategy](transaction-import-plan.md#transfer-strategy-cases-1--5). Store `ynab_paired_id` on both. |
| CC payment where the other leg's account was **not** migrated (excluded / Plaid read-only) | Import only the migrated leg as "Payment, Transfer"; the other leg is skipped, no grouping. |
| `category='Inflow: Ready to Assign'`, `amount=0`, payee `Starting Balance` | Skip (pure metadata) — decision-table case 3. |
| `category='Inflow: Ready to Assign'`, `amount≠0`, payee `Starting Balance` | Import as opening balance on the LM CC account — decision-table case 4/8. The negative/positive amount naturally seeds the liability balance for reconciliation. |
| `amount≠0`, payee `Manual Balance Adjustment` or `Reconciliation Balance Adjustment` | Import as a plain transaction, null category, preserve payee text. Flag in the dry-run summary — edge case 5. |
| CC interest / fees (manual expense with a real category) | Import as-is (normal expense). No special handling; visible in reconciliation so the user can verify. |

There is deliberately **no row** for "transaction carrying a Credit Card Payments
category," because the data confirms none exist. See the next section for how that
group is handled at the *category* level.

## The "Credit Card Payments" category group

**Decision: import it as a normal group.** (This replaces the earlier plan to skip
the group and hard-abort if any transaction referenced it.)

- In **Phase 0**, the "Credit Card Payments" group and its per-card categories
  (e.g. `Edu Credit CIBC Visa 💳`, `Costco Mastercard 🛒`, `Carol Credit CIBC
  Visa 💳`, `Canadian Tire Mastercard Credit 🔺` — 9 members in the CAD budget)
  are created in LM like any other group/categories, and recorded in sync_state.
- **Note on `internal: true`:** in the YNAB export the "Credit Card Payments"
  group carries `internal: true`, but its member categories do not. Phase 0 skips
  a group only when it has **no non-internal, non-deleted categories** (see
  `_build_category_plan` and [ynab-quirks.md](ynab-quirks.md)). Since the CC
  Payment categories are `internal: false`, the group passes through as normal —
  no special-casing needed.
- In **Phase 1**, **no transaction will ever be assigned to one of these
  categories**, because no YNAB transaction references them (confirmed in the
  export). They therefore end up as **empty categories** in LM.
- Empty Credit Card Payment categories are **harmless**: they hold no
  transactions, contribute nothing to totals or budgets, and do not affect
  reconciliation. They are simply leftover labels.
- **Post-import cleanup is optional and user-driven.** The migration notes /
  README should tell the user they may archive or delete the "Credit Card
  Payments" categories in LM if they don't want the clutter, since LM has no use
  for them. The importer should **not** delete them automatically (it never
  deletes user-visible data), but it *may* note their existence in the final
  summary.

### Why not skip the group?

Skipping was the previous approach, justified by "these categories are pure
budgeting metadata." That is true, but creating them is simpler and safer:

- It avoids a special-case branch in Phase 0 category creation.
- It removes the fragile pre-flight "abort if any transaction uses a CC Payment
  category" guard, which was protecting against a shape that does not occur.
- It is non-destructive and trivially reversible by the user (archive/delete).

If we later decide the empty categories are undesirable, the cleaner option is a
**post-import prompt** ("found N empty Credit Card Payment categories — archive
them?"), not a Phase 0 skip and not a hard abort.

## Open questions / edge cases

1. **Account type / subtype for cards.** This doc assumes the account mapping sets
   YNAB CC accounts to LM type `credit`. That mapping is tracked separately (see
   the account-type-mapping memory note); if a card falls back to "other asset,"
   its balance will be added to net worth instead of subtracted. Verify CC
   accounts resolve to `credit` before relying on net-worth numbers.

2. **Balance sign on opening-balance import.** A YNAB card with a balance owed
   imports its Starting Balance as a negative-in-YNAB amount; confirm the sign
   lands correctly on an LM Credit account so the reconciled balance shows the
   right "amount owed." Cross-check with
   [balance-reconciliation.md](balance-reconciliation.md).

3. **Pre-existing balance owed with no Starting Balance transaction.** If a card
   was added to YNAB with an opening balance entered directly (and `--since`
   excludes it), the opening-balance transaction may be filtered out. The
   date-filter exception (import Starting Balance regardless of cutoff) should
   cover this; verify for cards specifically.

4. **Foreign-currency cards.** Handled at the *account* level by the exclusion
   rules in [multi-budget.md](multi-budget.md). Transfer-skip logic must check
   exclusion on **both** legs of a payment so a payment to/from an excluded
   foreign card doesn't import a dangling single leg.

5. **Grouping payment legs.** The importer leaves the two payment legs ungrouped
   and stores `ynab_paired_id`; the future Transfer Management Tool can group
   them (and must **not** use LM's "Create transfer" action on already-imported
   transactions, which would create a spurious third entry). Cross-currency
   payment legs should be left ungrouped (FX makes the group total non-zero).

6. **Empty-category cleanup UX.** Decide whether the final summary just mentions
   the empty Credit Card Payment categories or offers an interactive
   archive/delete. Out of scope for the first Phase 1 cut; non-blocking.
