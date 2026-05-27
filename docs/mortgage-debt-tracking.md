# YNAB Mortgage and Debt Account Tracking

## Overview

YNAB tracks debt accounts (mortgages, auto loans, lines of credit) with both explicit transactions and account-level metadata that includes interest rate history.

## Account Metadata

Debt accounts contain these fields:
- `debt_interest_rates`: Dictionary mapping dates to interest rates (in basis points, e.g., 4900 = 4.9%)
- `debt_minimum_payments`: Dictionary mapping dates to minimum payment amounts (in milliunits)
- `debt_escrow_amounts`: Dictionary mapping dates to escrow/insurance amounts (in milliunits)

### Example Structure

```json
{
  "type": "mortgage",
  "debt_interest_rates": {
    "2023-06-01": 100,     // 1%
    "2026-03-01": 200      // 2% (rate changed at renewal)
  },
  "debt_minimum_payments": {
    "2023-06-01": 1000000,  // Amount in milliunits ($1,000.00)
    "2026-03-01": 100000    // Changed after renewal ($100.00)
  },
  "debt_escrow_amounts": {
    "2026-03-01": 0
  }
}
```

## Transaction vs. Balance

**CRITICAL FINDING**: The account balance includes accrued interest, but transactions only record actual payments made.

### How This Manifests

Observed pattern across debt accounts:
- Initial debt balance: $X
- Sum of payment transactions: $Y
- Current account balance: $Z
- If Z ≠ (X + Y), the difference represents **accrued interest** that YNAB calculated but did not create as explicit transactions

### How YNAB Updates Balances

1. **Explicit transactions**: Payment transactions are recorded normally
   - Payee: "Transfer : [checking account]"
   - Amount: Monthly payment amount
   - No "interest charged" line items

2. **Implicit interest accrual**: YNAB tracks via metadata only
   - Interest rates stored in `debt_interest_rates`
   - Rates change on renewal dates
   - Interest is *calculated* but not *transactioned*
   - Balance reflects real-world amount owed (principal + accrued interest)

3. **Special transaction types**: Debt accounts can have `debt_transaction_type` field
   - `"balanceAdjustment"` — manual corrections (e.g., BankCorp Mortgage had a $100.00 adjustment on 2026-03-23)

## Additional Debt Account Types

Observed patterns across different debt account types:
- **Mortgages**: Larger principal, longer duration, rate renewal history
- **Auto loans**: Fixed rate, shorter duration, higher interest rates
- **Lines of credit**: May have variable rates

All follow the same pattern: accrued interest embedded in balance, not in transactions.

## Implications for Lunch Money Import

### What Gets Imported
- ✅ Transaction history (payments made)
- ✅ Account balances (as-of export date)
- ✅ Account metadata (interest rates stored in `custom_metadata`)

### What's Lost
- ❌ Interest accrual is not explicit
- ❌ Lunch Money won't recalculate interest automatically
- ❌ Balance reconciliation will fail if Lunch Money doesn't account for accrued interest

### Recommendations

1. **Store interest data in `custom_metadata`**
   - Save `debt_interest_rates` and `debt_minimum_payments` on the LM account
   - Include historical rates for reference
   - Example: `{"ynab_interest_rates": {...}, "ynab_minimum_payments": {...}}`

2. **Balance reconciliation strategy**
   - After importing transactions, compare LM balance to YNAB balance
   - For mortgages, expect LM balance = YNAB balance (both include accrued interest in balance field)
   - If discrepancies exist, create an adjustment transaction dated at import time

3. **Interest tracking in Lunch Money**
   - Lunch Money has no API for debt-specific interest tracking (as of v2)
   - User would need to manually track interest separately if needed
   - Could use a separate category/transaction for interest accrual if desired

4. **Future re-imports**
   - Store interest rate history in sync state for auditing
   - Warn user if rates differ from YNAB on re-run (may indicate account changes)

## Technical Details

### Interest Rate Format
- Stored as basis points (100 = 1%)
- Example: 200 = 2% annual interest rate

### Amount Format (in transactions)
- Milliunits: divide by 1000 for currency value
- Example: 1000000 milliunits = $1,000.00

### Interest Calculation

Interest accrual can be estimated using simple interest or amortization:
- **Simple interest**: P × r × t (principal × rate × time)
- **Amortization**: More accurate for mortgages with fixed payments
- YNAB uses real-world account data (from bank), so actual accrual may include fees, escrow adjustments, etc.
- The balance difference between transactions sum and account balance reveals the interest/fee impact

## YNAB vs Lunch Money Concepts

| YNAB | Lunch Money | Notes |
|------|-------------|-------|
| `debt_interest_rates` | None (v2 API) | Must store in `custom_metadata` if needed |
| `debt_minimum_payments` | None (v2 API) | Must store in `custom_metadata` if needed |
| Balance = principal + interest | Balance = sum of txns + opening bal | Different calculation; may not match |
| Implicit interest accrual | Must be transactioned | Interest is not auto-calculated |
| Renewal rates (date-based) | None | Manual updates required |

## Implementation Notes

- YNAB exports include full `debt_interest_rates` and `debt_minimum_payments` history
- Transaction exports show only actual payments, not interest calculations
- Account balance field in YNAB export reflects real-world account state (principal + accrued interest)
- Importers must choose strategy: reconcile to YNAB balance or accept discrepancy
