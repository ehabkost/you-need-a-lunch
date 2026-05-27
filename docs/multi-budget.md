# Multi-Budget Scenarios

## BRL Accounts in the CAD Budget

The CAD YNAB budget contains BRL accounts that were only used to represent BRL balances — they are not real CAD accounts. There is a separate BRL YNAB budget that will eventually be imported into Lunch Money independently.

### Plan

When importing the CAD budget, these BRL accounts (and their transactions) should be excluded. The importer will need a per-account exclude/ignore mechanism — likely a flag or config that lets the user mark specific YNAB account IDs or names as excluded from a given import run.

This is low priority but the account-exclude feature should be kept in mind when designing the account import phase.

## Multi-Account Import Strategy

The same YNAB budget can be imported to multiple Lunch Money accounts. Sync state is stored per LM account:

- Local sync state path: `data/<slug>/<lm_account_id>/sync_state.json`
- This allows tracking separate account/category/transaction mappings for each target LM account
- Useful for splitting budgets across multiple users or accounts

Each import run will:
1. Fetch the current LM user's account ID
2. Create or load sync state for that specific account
3. Store mappings keyed by the LM account ID

This enables workflows like:
- Import CAD budget to User A's main account
- Later import same CAD budget to User B's collaborative account
- Each maintains separate YNAB↔LM mappings without conflicts
