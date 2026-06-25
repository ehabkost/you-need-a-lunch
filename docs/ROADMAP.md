# Roadmap & Status

Single source of truth for **what is done, what is next, what is blocked, and what is
deferred**. When this file disagrees with a feature doc about *priority, milestone, or
status*, **this file wins**. Feature docs remain authoritative for their own *design*.

Last reviewed: 2026-06-25.

## ⏳ Open decisions / needs review (parked for later)

Unresolved items to revisit before they block the work they touch. Not yet scheduled.

1. **BRL budget's v1 home.** Moving "two budgets → one LM account" to v2 leaves the BRL budget
   without a v1 story (`multi-currency-strategy.md` assumed it imports *into CAD's* LM account).
   Likely answer: **v1 = each YNAB budget → its own separate LM account; v2 = consolidating BRL
   into CAD's account.** Decide and record explicitly.
2. **Phase 2 has no design doc.** Next real importer after reconciliation, but unplanned. Needs:
   `budgeted` milliunit→decimal per category per month, and how YNAB rollover / Ready-to-Assign
   income settings map to LM's rollover options (KB budgeting pages referenced in CLAUDE.md but
   never turned into a plan). Gated on the write-API check (Gap §1).
3. **Command-surface naming.** Three read-only "checking" commands will coexist: `audit`
   (mapping integrity), `reconcile` (per-txn field drift), and the new **balance** reconciliation.
   Decide the verb/flag surface (e.g. `reconcile --balance` vs. a distinct verb) before building.
4. **Verify LM's own balance-computation semantics** before building reconciliation: does LM
   count split *children* vs. parent, and how do exclude-from-totals categories ("Payment,
   Transfer", "Tracking") affect an account's *balance* (vs. trends/totals)? KB check up front,
   or reconciliation reports false mismatches.
5. **Golden fixtures are CAD-only.** Importer tests build from `data/cad`. Add liability-sign,
   negative-income, and BRL/USD fixtures — reconciliation depends on those signs being right.

## Legend

| Mark | Status |
|---|---|
| ✅ | Implemented and in use |
| 🟡 | Partially built / plumbing exists, not finished |
| 🔭 | Planned, design doc exists, not started |
| ❓ | Blocked on an unverified external fact (usually an LM API capability) |
| 💤 | Deferred — explicitly not being worked on now |

Milestones:
- **v1 — Core migration.** Get YNAB → LM data migrated correctly for one budget at a time,
  with balances that reconcile. This is the product.
- **v2 — Multi-budget & enrichment.** Second budget into the same LM account, payee rules.
- **Someday — maybe-one-day.** Ideas worth keeping but not committed to.

## v1 — Core migration

| Phase / feature | Status | Depends on | Design doc |
|---|---|---|---|
| Phase 0 — Accounts | ✅ | — | [migration-plan.md](migration-plan.md) |
| Phase 0 — Categories | ✅ | accounts | [migration-plan.md](migration-plan.md), [ynab-quirks.md](ynab-quirks.md) |
| Phase 1 — Transactions (incl. native splits) | ✅ | Phase 0 | [transaction-import-plan.md](transaction-import-plan.md), [transaction-importer-implementation.md](transaction-importer-implementation.md) |
| Offline sync + read-only `reconcile` (field-diff) | ✅ | Phase 1 | archived `transaction-update-plan.md` |
| **Balance Reconciliation & Validation** | 🔭 **high prio** | Phase 1 | [balance-reconciliation.md](balance-reconciliation.md) |
| Phase 2 — Budget Assignments | ❓ unplanned | Phase 0 | *(none yet — see Gaps)* |
| Phase 3 — Budget Goals | ❓ unplanned | Phase 0 | *(none yet — see Gaps)* |
| Phase 4 — Scheduled Transactions (manual worklist) | 🔭 low prio | Phase 1 | [scheduled-transactions-import-plan.md](scheduled-transactions-import-plan.md) |

### The v1 critical path

```
Phase 0 ✅ ──► Phase 1 ✅ ──► Balance Reconciliation & Validation 🔭  ◄── do this next
                            └► Phase 2 (budget) ❓ ─┐
                            └► Phase 3 (goals)  ❓ ─┼► gated on LM write-API check
                            └► Phase 4 (worklist) 🔭┘
```

**Next action:** build **Balance Reconciliation & Validation**, then run the
**LM budget/goals write-API check** that decides whether Phase 2/3 are real importers or
degrade to worklists (see Gaps §1).

### Balance Reconciliation & Validation (newly promoted to high prio)

The whole point of the tool is "migrate without corrupting balances," so verifying that is a
first-class command, not a footnote. Distinct from the two existing read commands:

- `audit` — checks every **LM entity maps to a YNAB entity** (mapping integrity).
- `reconcile` — checks **per-transaction field drift** between LM and what YNAB would produce
  (one-way sync detection); refreshes the local id index. **Does not check balances.**
- **Balance Reconciliation (new)** — per account, compares the **summed LM balance** against
  YNAB's `cleared_balance` / `uncleared_balance` / `balance`, classifies any gap (transfers,
  Plaid overlap, debt-interest accrual, rounding), and reports a clear **pass/fail** per
  account. See [balance-reconciliation.md](balance-reconciliation.md) and the debt-interest
  caveat in [mortgage-debt-tracking.md](mortgage-debt-tracking.md).

## v2 — Multi-budget & enrichment

| Feature | Status | Notes | Design doc |
|---|---|---|---|
| Two YNAB budgets → one LM account (BRL history into CAD's LM account) | 🔭 v2 | Was tentatively "v1"; **moved to v2**. Gated on unresolved research (Q2: LM multi-currency category-balance semantics) and a category-separation decision (prefix vs. group). | [multi-currency-strategy.md](multi-currency-strategy.md), [multi-budget.md](multi-budget.md) |
| Payee rename + auto-category rules import | 🔭 v2 | **Demoted from Phase 0.5.** Needs Playwright UI automation + email/password creds (no LM rules API) — a different, brittler risk class than the API-clean importers. Revisit after v1. | [payee-rules-import-plan.md](payee-rules-import-plan.md) |
| Account-merging helper | 🔭 v2 | Supports the "sort out the mess" stage after the second budget lands. | [future-tools.md](future-tools.md) |

## Post-migration tools (v2+, run on demand against live LM)

These are maintenance/cleanup tools, not part of the import path. Roughly in value order:

| Tool | Status | Design doc |
|---|---|---|
| Transfer Management (pair + group transfer legs) | 🔭 | [future-tools.md](future-tools.md) |
| Transaction Matching (dedupe manual vs Plaid) | 🔭 | [future-tools.md](future-tools.md) |
| Tracking-Category Audit (catch budget leaks) | 🔭 | [future-tools.md](future-tools.md) |

## Someday — maybe-one-day ideas

| Idea | Status | Notes | Design doc |
|---|---|---|---|
| `--since` partial / date-range import | 💤 | **Deprioritized.** Plumbing partly exists (`TxnImportOptions.since`, filter in `transactions.py`) but the *balance story under partial import* (synthetic cutoff opening balance) is unfinished and collides with the real Starting-Balance import — see Gaps §2. Not a focus; full-history import is the default path. | [date-filtering.md](date-filtering.md) |
| YNAB4 / GnuCash historical backfill | 💤 | Pre-2017 BRL history (~12k txns) + ~2003 GnuCash. Same opening-balance double-count hazard as `--since`. | [ynab4-historical-import-idea.md](ynab4-historical-import-idea.md) |

## Cross-cutting gaps that need care (not phase-specific)

These are correctness/scoping risks that span phases. Numbered for reference.

1. **LM budget/goals write-API capability is unverified.** Phase 2 and Phase 3 assume LM
   lets us *write* budgeted amounts / goals. The recurring-items endpoint is read-only, so
   this is a real possibility for budgets/goals too. **Verify against the v2 OpenAPI spec +
   knowledge base before planning Phase 2.** If read-only, both phases degrade from importer
   to worklist (the Phase 4 shape) — a scope change to plan for, not discover.

2. **Opening-balance double-count risk.** Two mechanisms exist and are not reconciled:
   (a) import the real YNAB "Starting Balance" txn ignoring any cutoff
   ([transaction-import-plan.md](transaction-import-plan.md)); (b) synthesize a cutoff opening
   balance under `--since` ([date-filtering.md](date-filtering.md)). Under partial import an
   account could get **both** → doubled balance. Needs one explicit rule: the synthetic
   opening balance and the real Starting Balance are **mutually exclusive per account**. Same
   hazard family as the YNAB4 backfill — solve it once, generally. (Lower urgency now that
   `--since` is deferred, but the rule should be written before any partial-import work.)

3. **Production-blocking open questions still open** (from
   [transaction-importer-implementation.md](transaction-importer-implementation.md) §12):
   `PUT category_id: null` to uncategorize a split child; whether a split child can join a
   transaction group; whether Plaid inserts honor `external_id`. All are testable against the
   `.env.testing` LM account and should be closed before relying on the affected behavior.

4. **Debt-account interest accrual** can make balance reconciliation fail (interest is in the
   YNAB balance but never transactioned). Folds into the Balance Reconciliation feature: it
   must recognize and explain this gap rather than flag it as an error.
   See [mortgage-debt-tracking.md](mortgage-debt-tracking.md).

5. **Doc authority overlap.** Several docs assert their own scope/priority
   (`multi-currency-strategy.md` even says "when this disagrees with multi-budget.md, prefer
   this one"). This file is now the authority for status/priority/sequencing; feature docs
   should carry a one-line status banner pointing here and stop asserting milestones inline.
