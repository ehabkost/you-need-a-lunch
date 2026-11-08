---
name: ynab-api
description: Work with the YNAB (You Need A Budget) REST API from scripts - read budgets/plans, accounts, categories, payees, transactions, months, scheduled transactions, or write/update transactions. Use when the user asks to query, export, annotate or automate YNAB, or asks about YNAB data semantics (milliunits, sign, import_id, delta sync, internal categories, transfers, credit cards, debt accounts). Points to the you-need-a-lunch docs and code that already cover YNAB.
---

# YNAB API

Public REST API v1: base `https://api.ynab.com/v1`, `Authorization: Bearer <token>`,
**200 requests/hour per token** (HTTP 429 with `Retry-After`). Per-resource field lists are in
the repo docs below; the full spec is bundled, so don't rely on memory for field names.

Paths below are relative to the you-need-a-lunch repo root (in the life workspace that is
`repos/you-need-a-lunch/`).

## Auth

Token from env only (`YNAB_API_TOKEN`, optionally `YNAB_BUDGET_ID`), injected by 1Password:
`./prod-run.sh <cmd>` (uses `.env.production`) in this repo, or `op run --env-file=... --`
elsewhere. Never print or commit the token; for curl use the `op-run` skill. Missing variable
-> exit with a clear error naming it (repo rule, see `CLAUDE.md`).

## Where things are

| Need | Look at |
|---|---|
| Resource fields, endpoints, milliunit/sign convention, rate limit | `docs/api-reference.md` |
| Full OpenAPI spec (grep it, ~170 KB, don't read whole): `grep -n '^  /' docs/ynab-openapi.yaml` lists paths | `docs/ynab-openapi.yaml`, `docs/ynab-openapi.json` |
| Real-data deviations from the spec (e.g. `internal=true` on user groups) | `docs/ynab-quirks.md` |
| YNAB milliunits vs Lunch Money decimal strings, sign flip | `docs/amount-conversion.md` |
| Payee rename rules (not in public API; session-auth internal catalog endpoint) | `docs/ynab-internal-catalog-api.md` |
| `cleared`/`uncleared`/`balance` fields and why they drift | `docs/balance-reconciliation.md` |
| Credit card accounts, payment categories, transfers | `docs/credit-cards.md` |
| Debt/mortgage accounts (interest not shown as transactions) | `docs/mortgage-debt-tracking.md` |
| Every transaction shape / internal category and how it is treated | `docs/transaction-import-plan.md`, `docs/transaction-importer-implementation.md` |
| Scheduled transactions, payee rules | `docs/scheduled-transactions-import-plan.md`, `docs/payee-rules-import-plan.md` |
| Multiple budgets/currencies (CAD + BRL) | `docs/multi-budget.md`, `docs/multi-currency-strategy.md` |
| Older YNAB4 history | `docs/ynab4-historical-import-idea.md` |
| Date-range / `--since` handling | `docs/date-filtering.md` |
| Project status and priorities | `docs/ROADMAP.md`, `docs/migration-plan.md`, `CLAUDE.md` |
| Existing YNAB API client with 429 retry and delta sync | `ynab/export.py` (`YNABClient`, checkpoint of `server_knowledge`) |
| Viewing exported data | `ynab/view.py` |
| Live probes of write behaviour (pattern for testing safely) | `scripts/probe_put_notes.py`, `scripts/probe_recurring_reinsert.py` |
| Another working YNAB client (read + PATCH/split memos) | `lib/ynab_amazon.py` and `bin/ynab-amazon` in the life repo; `ynab-amazon` skill |

The Lunch Money side (`lunchmoney/`, `docs/api-validation.md`, `docs/API-VALIDATION-GUIDE.md`,
`docs/PYTHON-CLIENT-OPTIONS.md`, `docs/TYPE-CHECKING-GUIDE.md`) is only relevant when the task
involves the migration target.

## Key facts and quirks

- **Milliunits**: amounts are integers, 1000 = 1 currency unit; negative = outflow. Convert
  with `round(x * 1000)`; never send floats. (Lunch Money has the opposite sign.)
- **`plans` vs `budgets`**: the bundled spec and `docs/api-reference.md` use
  `/plans/{plan_id}/...`; older scripts and docs use `/budgets/{budget_id}/...` for the same
  thing. Prefer the spec's spelling and check the spec when unsure.
- **Delta sync**: list endpoints take `last_knowledge_of_server=<n>` and return
  `server_knowledge`; store it per resource and merge changes by `id`. Deleted records come
  back with `deleted: true` - keep them in exports, skip them when acting.
- **Transactions**: `since_date=YYYY-MM-DD` and `type=uncategorized|unapproved` filter the
  list. Splits are `subtransactions[]`; a transfer has `transfer_account_id`. YNAB `date` is
  when it happened, usually 1-3 days before the bank's posted date, so match bank exports on
  amount + a +/-3 day window (consume matches so each YNAB row is used once).
- **`import_id`**: file/bank imports get `YNAB:<milliunits>:<date>:<occurrence>`. When you
  create transactions yourself, set your own unique `import_id` (max 36 chars) so re-runs
  dedupe instead of duplicating.
- **Internal categories** (`internal: true`): "Inflow: Ready to Assign", "Starting Balance",
  etc. need special handling, and the `internal` flag on *groups* is unreliable - see
  `docs/ynab-quirks.md` before filtering on it.
- **Not in the public API**: payee rename rules and some UI-only data (see the catalog doc).
- **Writes**: per the workspace rules, show a summary and get confirmation before any POST,
  PATCH or DELETE; prefer a dry-run mode. Test write semantics on one transaction and restore
  it (see `scripts/probe_*.py`). Never auto-categorize unless asked.
- Wrap HTTP errors and print the response body: 401 = bad/expired token, 404 = wrong
  budget/account id, 422 = malformed payload, 429 = wait `Retry-After`.
