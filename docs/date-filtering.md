# Date-Range Filtering: --since Option

> **Status: 💤 deferred (maybe-one-day) — see [ROADMAP.md](ROADMAP.md).** Plumbing partly
> exists, but partial import is not a focus; full-history import is the default path. The
> opening-balance story below is unfinished (see ROADMAP Gap §2 — double-count risk).

## Overview

The importer supports a `--since DATE` option (e.g. `--since 2023-01-01` or `--since 6mo` or `--since 2y`) that restricts which data gets imported. This is the primary way to do a partial migration — e.g. "only bring in the last 2 years" — without importing the full history.

## What gets filtered by date

| Resource | Filter applied |
|---|---|
| Transactions | `date >= since` |
| Budget assignments | month `>= since` (truncated to month boundary) |
| Scheduled transactions | `next_due_date >= since` (future-facing; always import if active) |
| Accounts | Never filtered — always imported regardless of `--since` |
| Categories | Never filtered — always imported regardless of `--since` |
| Budget goals | Never filtered — goals are on categories, not time-bound |

## Balance Reconciliation Under Partial Import

When importing with `--since`, account balances in Lunch Money will **not** match YNAB's current balance because pre-cutoff transactions are absent. To handle this:

1. For each account, compute the YNAB balance as of the cutoff date (sum of all transactions before `since`)
2. Create an **opening balance transaction** in Lunch Money dated one day before `since`, with that amount, in a designated "Opening Balance" or "Adjustments" category
3. After importing all post-cutoff transactions, verify that Lunch Money balance = YNAB current balance
4. The opening balance transaction should be stored with `custom_metadata: {"ynab_opening_balance": true, "as_of": "YYYY-MM-DD"}` so it can be identified and updated on re-runs

This ensures balances always reconcile regardless of the chosen cutoff date.

## Interaction with Deduplication

The `--since` filter is applied **after** deduplication checks. If a transaction already exists in Lunch Money (by `custom_metadata.ynab_id`) but falls before the cutoff, it is still skipped — the filter only prevents *new* imports of old data, not removal of already-imported data.

## Date Format Examples

- `--since 2023-01-01` — absolute date
- `--since 6mo` — relative (6 months ago)
- `--since 2y` — relative (2 years ago)
