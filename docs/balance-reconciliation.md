# Balance Reconciliation Strategy

> **Status: 🔭 high-priority v1, not yet implemented — see [ROADMAP.md](ROADMAP.md).** This is
> the next thing to build after Phase 1. It is *not* the existing `reconcile` command (that
> does per-transaction field-diff, not balances).

## Goal

Lunch Money account balances should match YNAB after import.

## YNAB Balance Fields

YNAB provides two balances per account:
- `cleared_balance`: sum of cleared + reconciled transactions
- `uncleared_balance`: sum of uncleared transactions
- `balance` = `cleared_balance` + `uncleared_balance`

## Reconciliation Process

After importing transactions:

1. Compute the sum of imported transactions per account in Lunch Money
2. Compare against YNAB's `balance`
3. If there's a discrepancy, identify cause: skipped transfers, Plaid overlap, or rounding
4. Optionally create a reconciliation/adjustment transaction to force-match balances

## Known Discrepancy Risks

- **Transfer transactions**: double-counted if both legs imported, zero if neither
- **Transactions in Plaid-linked accounts**: may be skipped
- **Starting balance transactions**: each account may have an initial "Starting Balance" entry
- **Milliunits rounding**: should not occur if dividing exactly by 1000
- **Currency conversion**: for multi-currency budgets
- **Debt account interest**: accrued interest may not appear as explicit transactions (see [[mortgage-debt-tracking]])

## Special Case: Debt Accounts

Debt accounts (mortgages, loans) have special handling:
- Balance includes accrued interest that is not explicitly transactioned
- Interest rates and minimum payments are stored in `debt_interest_rates` and `debt_minimum_payments`
- This may cause balance mismatches if Lunch Money doesn't account for the same interest accrual
- See [[mortgage-debt-tracking]] for details

## Partial Imports

See [[date-filtering]] for balance reconciliation strategy when importing with `--since` date filter.
