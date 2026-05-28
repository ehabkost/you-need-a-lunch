# you-need-a-lunch

A tool for migrating data from [YNAB](https://www.ynab.com/) (You Need A Budget) to [Lunch Money](https://lunchmoney.app/) using their respective APIs.

## Status and scope

This is a **personal-use, work-in-progress** project. It is being built around one specific YNAB → Lunch Money migration (a multi-currency CAD/BRL setup with a mix of manual and Plaid-linked accounts), and many design decisions are anchored to that scenario.

It is not (yet) a polished, plug-and-play tool. In particular:

- Some configuration is hardcoded or assumed to match the author's situation.
- A few flows still require manual intervention or post-import cleanup.
- It is **one-way only** (YNAB → Lunch Money) and not a sync tool.
- It is not intended to be a generic importer/exporter for other budgeting services.

That said, the architecture is deliberately straightforward and most of the budget/migration-specific logic is isolated, so it may grow into a more reusable tool over time. Contributions, forks, and adaptations are welcome.

## Why it might still be useful to you

Even if you don't run the code as-is, the [`docs/`](docs/) directory is probably the most thorough public write-up of YNAB → Lunch Money migration corner cases that exists right now. It covers:

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

Key principles:

1. **Always show a dry-run summary first.** No writes happen without a per-resource-type count of creates/skips/conflicts and an explicit confirmation.
2. **Never duplicate.** Every imported entity carries a YNAB identifier in `custom_metadata.ynab_id` (or `external_id` for accounts) so re-runs are safe.
3. **Never touch Plaid-linked Lunch Money accounts** where `allow_transaction_modification: false`.
4. **Secrets only via environment variables.** No config files, no prompts, no hardcoded tokens.

See [CLAUDE.md](CLAUDE.md) for the full set of project conventions and anti-duplication rules.

## Running it

Two environments are supported via wrapper scripts that inject secrets from `.env.production` / `.env.testing` (both gitignored):

```sh
./prod-run.sh python ynab/export.py
./test-run.sh python lunchmoney/import.py --since 2y
```

Required environment variables:

| Variable | Used by |
|---|---|
| `YNAB_API_TOKEN`       | `ynab/` |
| `YNAB_BUDGET_ID`       | `ynab/` |
| `LUNCHMONEY_API_TOKEN` | `lunchmoney/` |

The wrappers currently call `wsl-op-run` (a 1Password-CLI wrapper for WSL). If you're not using that setup, swap the wrappers for a plain `dotenv`/`direnv`/`export`-based loader — nothing else in the codebase depends on 1Password.

## License

No license file yet. Until one is added, treat the contents as "all rights reserved" by default. If you want to use or adapt the code, please open an issue first.
