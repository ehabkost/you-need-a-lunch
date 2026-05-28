# you-need-a-lunch

A tool for migrating data from [YNAB](https://www.ynab.com/) (You Need A Budget) to [Lunch Money](https://lunchmoney.app/) using their respective APIs.

## Status and scope

This is a **personal-use, work-in-progress** project. It is being built around one specific YNAB → Lunch Money migration (a multi-currency CAD/BRL setup with a mix of manual and Plaid-linked accounts), and many design decisions are anchored to that scenario.

It is not (yet) a polished, plug-and-play tool. In particular:

- Some configuration is hardcoded or assumed to match the author's situation.
- A few flows still require manual intervention or post-import cleanup.
- It is **one-way only** (YNAB → Lunch Money) and not a sync tool.
- It is not intended to be a generic importer/exporter for other budgeting services.

## Why it might still be useful to you

Even if you don't run the code as-is, the [`docs/`](docs/) directory may be useful for covering the corner cases of a full YNAB → Lunch Money migration. It includes:

- [Migration plan](docs/migration-plan.md) — the phased approach (accounts → categories → transactions → budgets → goals → schedules).
- [Transaction import plan](docs/transaction-import-plan.md) — full decision table for every internal YNAB category and transaction shape, including transfers, credit cards, opening balances, and "Inflow: Ready to Assign".
- [Balance reconciliation](docs/balance-reconciliation.md) — how YNAB's `cleared`/`uncleared`/`balance` fields map onto Lunch Money and where discrepancies come from.
- [Mortgage & debt account tracking](docs/mortgage-debt-tracking.md) — the gnarly case of accrued interest in YNAB debt accounts that doesn't appear as explicit transactions.
- [Multi-currency strategy](docs/multi-currency-strategy.md) and [multi-budget scenarios](docs/multi-budget.md) — handling more than one YNAB budget feeding one Lunch Money account, BRL-proxy accounts in a CAD budget, cross-currency transfers, etc.
- [Date-range filtering](docs/date-filtering.md) — partial migrations (e.g. "only the last 2 years") and their interaction with opening-balance synthesis.
- [API reference](docs/api-reference.md) and [API validation notes](docs/api-validation.md) — known LM v2 API quirks (milliunits vs decimal strings, `closed_on` requiring a date not a boolean, etc.).
- [Future tools](docs/future-tools.md) — sketches for a post-migration transaction-matching tool and transfer-grouping tool.

If you are about to do a YNAB → Lunch Money migration yourself, reading those docs first will probably save you a few surprises.

## High-level approach

```
ynab/        # reads from YNAB API, writes to local JSON files
lunchmoney/  # reads local JSON files, writes to Lunch Money API
data/        # intermediate export files (gitignored)
```

The exporter and importer are independent scripts: the YNAB export is the source of truth for the import phase, which means you can re-run the importer against a stable snapshot.

See [CLAUDE.md](CLAUDE.md) for the full set of project conventions and anti-duplication rules.

## Running it

Secrets are read exclusively from environment variables — no config files, no prompts. Set the following before invoking the scripts (via your preferred mechanism: shell exports, `direnv`, `dotenv`, 1Password CLI, etc.):

| Variable | Used by |
|---|---|
| `YNAB_API_TOKEN`       | `ynab/` |
| `YNAB_BUDGET_ID`       | `ynab/` |
| `LUNCHMONEY_API_TOKEN` | `lunchmoney/` |

If a required variable is missing, the tool exits immediately with a clear error message naming it.

Main entry points:

```sh
python ynab/export.py                  # dump the YNAB budget to data/<slug>/
python lunchmoney/import.py import     # dry-run summary of what would be imported
python lunchmoney/import.py import --apply   # actually write to Lunch Money
```

`lunchmoney/import.py` also has `show-mapping`, `audit`, and `fix-mapping` subcommands; see `--help` for details.
