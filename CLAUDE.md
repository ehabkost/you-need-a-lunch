# you-need-a-lunch

A tool to migrate data from YNAB (You Need A Budget) to Lunchmoney.app using their respective APIs.

## Goal

Export all data from YNAB, then import it into Lunch Money — carefully avoiding duplication of any data already present. A summary of planned changes is always shown before applying them.

The tool is intentionally scoped to one-way migration for now. It is not a sync tool, and is not designed to be a generic importer/exporter for other services.

## Project Structure (planned)

```
exporter/   # reads from YNAB API, writes to local JSON files
importer/   # reads local JSON files, writes to Lunch Money API
data/       # intermediate export files (gitignored)
```

## Secrets and Authentication

All tools must read secrets exclusively from environment variables — no config files, no hardcoded values, no interactive prompts for credentials. Secrets are injected via `wsl-op-run` using the wrapper scripts:

```sh
./prod-run.sh python exporter/export.py       # uses .env.production
./test-run.sh python importer/import.py --since 2y  # uses .env.testing
```

- `.env.production` — production YNAB + Lunch Money credentials
- `.env.testing` — test Lunch Money account only (no YNAB token for now)

### Environment variables

| Variable | Used by |
|---|---|
| `YNAB_API_TOKEN` | exporter |
| `YNAB_BUDGET_ID` | exporter |
| `LUNCHMONEY_API_TOKEN` | importer |

If a required variable is missing, the tool must exit immediately with a clear error message naming the missing variable — never silently fall back or prompt.

### API details
- **YNAB**: Bearer token via `Authorization: Bearer TOKEN`. Rate limit: 200 requests/hour per token.
- **Lunch Money**: Bearer token via `Authorization: Bearer TOKEN`. Base URL: `https://api.lunchmoney.dev/v2`. Mock server for testing: `https://mock.lunchmoney.dev/v2`.

## API Reference

See **[docs/api-reference.md](docs/api-reference.md)** for details on YNAB and Lunch Money v2 APIs, including:
- Data types and fields for each resource
- Amount conventions (YNAB milliunits vs. LM decimal strings)
- Authentication and rate limits
- Account types and metadata storage

## Migration Plan

See **[docs/migration-plan.md](docs/migration-plan.md)** for the detailed phased plan:
- **Phase 0**: Accounts and Categories (matching and creation)
- **Phase 1**: Transactions (conversion and deduplication)
- **Phase 2-4**: Budget Assignments, Goals, and Scheduled Transactions

Each phase includes dry-run summary and user confirmation before applying changes.

## Balance Reconciliation

See **[docs/balance-reconciliation.md](docs/balance-reconciliation.md)** for:
- YNAB balance fields (`cleared_balance`, `uncleared_balance`, `balance`)
- Post-import reconciliation process
- Known discrepancy risks (transfers, Plaid overlap, debt interest, etc.)

## Anti-Duplication Rules

1. Always check if a Lunch Money account with matching `external_id` already exists before creating
2. Always check transaction `custom_metadata.ynab_id` before inserting (for re-runs)
3. Respect the `skipped_duplicates` response from Lunch Money POST /transactions
4. Never import into Plaid-linked Lunch Money accounts where `allow_transaction_modification: false`
5. Never import YNAB accounts that have been matched to existing Plaid accounts

## Date-Range Filtering

See **[docs/date-filtering.md](docs/date-filtering.md)** for the `--since DATE` option:
- Partial migration support (e.g. `--since 2y` for last 2 years)
- Per-resource filtering rules (transactions, budgets, schedules)
- Opening balance transaction strategy for balance reconciliation
- Interaction with deduplication

## Planned Post-Migration Tools

See **[docs/future-tools.md](docs/future-tools.md)** for details on tools to be built after migration:
- **Transaction Matching Tool**: identify and resolve duplicate transactions (manual vs. Plaid/imported)
- **Transfer Management Tool**: categorize and group transfer pairs

## Multi-Budget Scenarios

See **[docs/multi-budget.md](docs/multi-budget.md)** for:
- BRL accounts in CAD budget (account exclusion strategy)
- Multi-account import support (same YNAB budget → multiple LM accounts)
- Sync state organization by LM account ID

## Implementation Notes

- Importer must always print a **dry-run summary** (counts of creates/skips/conflicts per resource type) and prompt for confirmation before writing anything
- Test against real Lunch Money test account using `.env.testing` (more realistic than mock server)
- Export files in `data/` are the source of truth for the import phase — exporter and importer are independent scripts
- YNAB `deleted: true` records should be exported (for completeness) but not imported
- YNAB internal categories (e.g. "Inflow: Ready to Assign") have `internal: true` — skip during import
- Local sync state is stored in `data/<slug>/<lm_account_id>/sync_state.json` — records YNAB↔LM ID mappings for accounts, categories, and transactions, keyed by LM account ID. This allows the same YNAB budget to be imported to multiple LM accounts. Machine-generated; do not edit manually.
- LM metadata storage: accounts use `external_id` (YNAB UUID) and `custom_metadata` (YNAB type/flags); categories and transactions use `custom_metadata` only (no `external_id` field available)
