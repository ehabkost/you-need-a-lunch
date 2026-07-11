# Roadmap & Status

Single source of truth for **what is done, what is next, what is blocked, and what is
deferred**. When this file disagrees with a feature doc about *priority, milestone, or
status*, **this file wins**. Feature docs remain authoritative for their own *design*.

Last reviewed: 2026-07-11.

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
| Phase 2 — Budget Assignments | 🔭 planned | Phase 0 | *(none yet — see Gaps §1 resolution below)* |
| Phase 3 — Budget Goals/Targets | 🔭 planned, folds into Phase 2 | Phase 0, Phase 2 | *(none yet — see Gaps §1 and §6)* |
| Phase 4 — Scheduled Transactions (manual worklist) | 🔭 low prio | Phase 1 | [scheduled-transactions-import-plan.md](scheduled-transactions-import-plan.md) |

### The v1 critical path

```
Phase 0 ✅ ──► Phase 1 ✅ ──► Balance Reconciliation & Validation 🔭  ◄── do this next
                            └► Phase 2 (budget assignments) 🔭 ──► Phase 3 (goal-derived amounts) 🔭
                                                              └──► extend Balance Reconciliation to
                                                                   check budget assignments too
                            └► Phase 4 (worklist) 🔭
```

**Next action:** build **Balance Reconciliation & Validation** (account balance, with
month-end reconstruction from transaction history for prior months, not just current
balance), then **Phase 2 — Budget Assignments**, then extend the reconciliation tool to
also diff budget assignments (not just balances). See Gaps §1 for why Phase 2/3 are no
longer gated.

### Balance Reconciliation & Validation (newly promoted to high prio)

The whole point of the tool is "migrate without corrupting balances," so verifying that is a
first-class command, not a footnote. Distinct from the two existing read commands:

- `audit` — checks every **LM entity maps to a YNAB entity** (mapping integrity).
- `reconcile` — checks **per-transaction field drift** between LM and what YNAB would produce
  (one-way sync detection); refreshes the local id index. **Does not check balances.**
- **Balance Reconciliation (new)** — per account, compares the **summed LM balance** against
  YNAB's `cleared_balance` / `uncleared_balance` / `balance`, classifies any gap (transfers,
  Plaid overlap, debt-interest accrual, rounding), and reports a clear **pass/fail** per
  account. Scope confirmed 2026-07-11: checks the **latest month and prior months**, not just
  current balance — YNAB's API only exposes *current* balance fields (no month-end-balance
  endpoint), so prior-month checks require **reconstructing month-end balances from
  transaction history** on both sides, not a field read. See
  [balance-reconciliation.md](balance-reconciliation.md) and the debt-interest caveat in
  [mortgage-debt-tracking.md](mortgage-debt-tracking.md).
- **Budget Assignment Reconciliation (new, extends the above)** — once Phase 2 exists,
  extend this same tool to also diff **budget assignments** (LM `amount` per category/period
  vs. YNAB `budgeted`) alongside balances, per month. Depends on Phase 2 having imported
  assignments to diff against.

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
| Goals Service — standalone tool that recreates YNAB-style goals on top of LM's amount-only budget API | 💤 idea, no design | Runs indefinitely post-migration, not gated on YNAB. Computes each period's implied amount from user-defined goals and writes it via `PUT /budgets`, since LM can't store or compute goals itself (Gap §6). | [future-tools.md](future-tools.md) |
| "Lunch Money Added Service" (LMAS) — umbrella always-on service hosting Goals/Reconciliation/Transfer/Tracking-Audit as modules, with the YNAB→LM migration as just one feature/entry point | 💤 speculative umbrella, no design | Not on the critical path; explicitly deferred until 2-3 of the individual modules are built standalone first. See doc for why. | [lm-added-service-idea.md](lm-added-service-idea.md) |

## Cross-cutting gaps that need care (not phase-specific)

These are correctness/scoping risks that span phases. Numbered for reference.

1. ~~**LM budget/goals write-API capability is unverified.**~~ **Resolved 2026-07-11.**
   Checked `docs/lunchmoney-api-v2.json` + KB:
   - `PUT /budgets` (`upsertBudget`) is a real per-period upsert: `category_id`, `start_date`,
     `amount`, `currency`, `notes`. **Phase 2 (Budget Assignments) is a real importer.**
   - There is no `/goals` endpoint, and no separate "target" object in the schema at all. The
     KB budgeting guide confirms why: LM "targets" (fixed amount / fill-to-amount / match
     previous spend / etc.) are UI-only *presets* for computing a period's `amount`, not a
     stored resource distinct from the budget amount. So **Phase 3 doesn't map onto a separate
     LM write** — the only correct shape is: compute what YNAB's goal implies for each period,
     and write it as a plain `amount` via the same `/budgets` PUT as Phase 2. Phase 3 folds
     into Phase 2 as an amount-source, not a separate phase/endpoint.
   - Also confirmed `auto_budget_type`, `auto_budget_amount`, and `rollover_option` are
     `readOnly` on `budgetObject` and absent from `upsertBudgetRequestObject`. **There is no
     API to configure LM's recurring auto-budget preset or rollover behavior** — only a
     literal amount for one period at a time.
   - Cross-checked v1 (`https://alpha.lunchmoney.dev/v1/budget`): same limitation. `GET
     /v1/budgets` exposes richer preset metadata (`config.auto_suggest`:
     `budgeted`/`fixed`/`fixed-rollover`/`spent`, `data.is_automated`), but `PUT /v1/budgets`
     only accepts `start_date`, `category_id`, `amount`, `currency` — presets are read-only
     there too. No API version lets us write presets/rollover config, v1 or v2.
   - Replicating YNAB's *ongoing* target behavior (vs. a one-time snapshot of the amounts it
     currently implies) requires manual UI setup;
     document this as a known limitation, don't try to build around it.

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

6. **LM has no "goals" concept at all — product gap, not just an API gap.** Discovered
   2026-07-11 while scoping Phase 3. This is separate from Gap §1 (which is about *write API*
   availability): even in the LM **UI**, with no API involved, there is no equivalent to
   YNAB's target/goal features. Confirmed against the KB (`support.lunchmoney.app/guides/
   budgeting/faq`):
   - **No long-term / target-by-date goals at all.** "How do I set up goals in Lunch Money?"
     → *"This feature will be coming soon! Stay tuned by upvoting the feature request..."* —
     unshipped on LM's side. There is no field, UI or API, for a target amount + target date
     with progress tracking (e.g. "save $10,000 by 2028").
   - **No recurring/annual per-category budget cadence.** "Can I have different periods for
     different categories?" → *"Currently, all categories share the same budget period."* The
     budget period (monthly/weekly/biweekly/custom) is one account-wide setting; you cannot
     put a single category on a yearly cycle (e.g. "$1,200/year property tax") while the rest
     of the budget stays monthly. **Recurring Items** support an annual cadence, but that's a
     projection/reminder tool (expected-vs-missed tracking), not a budget-period override.
   - **The only documented workaround for either case is the manual sinking-fund pattern:**
     budget a monthly slice into the category, set rollover to "same category" so the balance
     accumulates, and track the target yourself outside LM. There is no progress bar, no
     target field, nothing the importer can populate to reproduce YNAB's goal UX.
   - **Consequence for this project:** confirms Gap §1's conclusion that **Phase 3 has nothing
     to import goals *into*** — not a sequencing/API problem to solve, a feature LM doesn't
     have. It's a genuine **post-migration workflow gap** for anyone who relies on YNAB goals
     day-to-day (long-term savings targets, per-category annual budgets): they lose that UX on
     migration and must rebuild it manually via rollover categories. Worth surfacing to the
     user explicitly before migration, not just noting in code — this is a product fit
     question, not something more API research or tool-building can fix.
   - **Idea captured, not scoped:** a standalone **Goals Service** could recreate this outside
     LM — user-defined goals computed client-side, written as plain `PUT /budgets` amounts
     each period. See [future-tools.md](future-tools.md#goals-service-recreates-ynab-goals-on-top-of-lms-amount-only-budget-api).
     This doesn't fix the LM product gap, it works around it — a separate always-on tool, not
     a migration-phase deliverable.
