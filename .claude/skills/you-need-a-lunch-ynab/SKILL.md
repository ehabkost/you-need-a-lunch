---
name: you-need-a-lunch-ynab
description: Work with the YNAB (You Need A Budget) REST API from scripts - read budgets/plans, accounts, categories, payees, transactions, months, scheduled transactions, or write/update transactions. Use when the user asks to query, export, annotate or automate YNAB, or asks about YNAB data semantics (milliunits, sign, import_id, delta sync, internal categories, transfers, credit cards, debt accounts). Points to the you-need-a-lunch docs and code that already cover YNAB.
---

# YNAB API: you-need-a-lunch docs and code

The generic `ynab-api` skill (personal, in dotfiles) covers setup with `op run`, OFX/CSV
matching and `ynab-check-missing`. This one is the project-specific map of what this repo
already documents and implements; use both.

Public REST API v1: base `https://api.ynab.com/v1`, `Authorization: Bearer <token>`,
**200 requests/hour per token, enforced in a rolling one-hour window** (official docs). Over the
limit: HTTP 429 with error name `too_many_requests`; the docs don't mention `Retry-After` (our
clients read it if present and fall back to a fixed wait), and since v1.73.0 no `X-Rate-Limit`
header is sent with a 429. Per-resource field lists are in the repo docs below; the full spec is
bundled, so don't rely on memory for field names.

**The bundled spec is v1.84.0** (May 2026); the live API is newer. For anything that might have
changed since, check the changelog at https://api.ynab.com/#changelog. Changes after v1.84.0
that matter here: v1.85.0 (`since_date` default and `until_date`, see Transactions below) and
v1.88.0 (`POST /plans/{plan_id}/transactions/import` is deprecated: accepted, but imports
nothing and returns an empty id list).

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
| Existing YNAB API client with 429 retry and delta sync | `ynab/export.py` (`YNABClient`, checkpoint of `server_knowledge`). Transactions are always fetched with `since_date=1900-01-01` (`FULL_HISTORY_SINCE_DATE`) because of the v1.85.0 default |
| Viewing exported data | `ynab/view.py` |
| Live probes of write behaviour (pattern for testing safely) | `scripts/probe_put_notes.py`, `scripts/probe_recurring_reinsert.py` |
| Another working YNAB client (read + PATCH/split memos) | `lib/ynab_amazon.py` and `bin/ynab-amazon` in the life repo; `ynab-amazon` skill |

The Lunch Money side (`lunchmoney/`, `docs/api-validation.md`, `docs/API-VALIDATION-GUIDE.md`,
`docs/PYTHON-CLIENT-OPTIONS.md`, `docs/TYPE-CHECKING-GUIDE.md`) is only relevant when the task
involves the migration target.

## Key facts and quirks

- **Milliunits**: amounts are integers, 1000 = 1 currency unit; negative = outflow. Convert
  with `round(x * 1000)`; never send floats. (Lunch Money has the opposite sign.)
- **`plans` vs `budgets`**: since v1.79.0 the documented paths are `/plans/{plan_id}/...` and
  response keys say `plans`, `default_plan`, `plan`. The old `/budgets/{budget_id}/...` paths
  still work (checked 2026-11-21 with read-only GETs on `/plans` and `/budgets`, both 200) and
  return the **old key names** (`budgets`, `default_budget`), but are no longer documented. Older
  scripts here (`ynab/export.py`) use `/budgets`; prefer `/plans` in new code and don't mix the
  two spellings' response keys.
- **Delta sync**: `last_knowledge_of_server=<n>` (response: `server_knowledge`) is accepted by
  the single plan, accounts, categories, payees, months, scheduled transactions and all five
  transaction lists (plan, account, category, payee, month). Not by category groups, payee
  locations or money movements. Store it per resource and merge changes by `id`. Deleted
  records come back with `deleted: true` - keep them in exports, skip them when acting.
- **Transactions**: `since_date=YYYY-MM-DD` and `until_date` (v1.85.0) work on all five
  transaction lists; `type=uncategorized|unapproved` only on the plan and account lists.
  **Since v1.85.0 `since_date` defaults to one year ago**, even with
  `last_knowledge_of_server=0` (checked live 2026-11-21): pass an explicit old `since_date`
  (e.g. `2000-01-01`) to get full history. Splits are `subtransactions[]`; a transfer has `transfer_account_id`. YNAB `date` is
  when it happened, usually 1-3 days before the bank's posted date, so match bank exports on
  amount + a +/-3 day window (consume matches so each YNAB row is used once).
- **`import_id`**: file/bank imports get `YNAB:<milliunits>:<date>:<occurrence>`. When you
  create transactions yourself, set your own unique `import_id` (max 36 chars, `maxLength` in
  the spec for create and update-by-`import_id`; an existing `import_id` can't be changed) so re-runs
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
