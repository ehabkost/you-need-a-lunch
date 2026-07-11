# Idea: "Lunch Money Added Service" (LMAS)

> **Status: 💤 someday idea, no design work done.** Speculative — not scoped, not scheduled,
> not gated into any milestone. See [ROADMAP.md](ROADMAP.md) for the authoritative status.

## The idea

Everything this project has built so far falls into two buckets:

1. **A one-shot migration tool** (`ynab/` → `data/` → `lunchmoney/`): run once (or a few
   times) per budget, then done.
2. **A growing pile of standalone "fills a gap in LM" tools**, discovered along the way,
   each currently planned as its own separate script: [Transaction Matching, Transfer
   Management, Tracking-Category Audit, Account-Merging Helper](future-tools.md), the
   [Goals Service](future-tools.md#goals-service-recreates-ynab-goals-on-top-of-lms-amount-only-budget-api),
   and the ongoing [Balance / Budget-Assignment Reconciliation](ROADMAP.md) checks.

Bucket 2 keeps growing every time we hit something LM's API/UI doesn't cover, and every
item in it independently needs: an LM API token, some local persisted state, a dry-run-first
pattern, and (for several of them) a recurring schedule rather than a one-off run.

The idea: instead of N separate scripts each reinventing that plumbing, build one small
**always-on service that sits next to a user's Lunch Money account** and hosts all of these
as modules/features. Call it the **Lunch Money Added Service (LMAS)** for now — working name,
not a commitment.

The one-time YNAB→LM migration this repo already does would become **one entry point/feature
of LMAS**, not a separate thing — "import my YNAB budget" is just the first-run mode of a
service that then keeps running.

## Why this might be worth doing (someday)

- **Shared infrastructure**, built once instead of five times: auth/token handling, local
  state store, dry-run/confirm UX, scheduling, a reporting/notification surface.
- **Compounding value**: reconciliation, goals, transfer pairing, and tracking-audit all
  benefit from the *same* underlying LM data cache and the *same* "here's what changed,
  confirm before I write" review loop. Building them as isolated scripts means re-solving
  that loop each time.
- **Migration becomes a feature, not a project.** If LMAS exists as a real thing, "migrate
  from YNAB" is just the onboarding flow for a new user of the service — potentially useful
  to other YNAB refugees, not just this one migration.

## Why this is explicitly *not* being pursued now

- The migration itself isn't done (see [ROADMAP.md](ROADMAP.md) — Balance Reconciliation and
  Phase 2/3 are still ahead of this).
- Every module candidate below is itself unbuilt and unscoped as a *standalone* tool first.
  Premature to design shared infrastructure for tools that don't exist yet — the shared
  shape should emerge from building 2-3 of them separately, not be guessed upfront.
- Turning this into something other people could run raises real scope: multi-user auth,
  hosting, holding other people's LM API tokens, support burden. None of that has been
  thought through, and it's a different risk class from "I run a script against my own
  account." If this ever gets pursued, start single-user/self-hosted only.

## Candidate modules (pulled from work already identified elsewhere)

| Module | Cadence | Already documented at |
|---|---|---|
| YNAB → LM migration (Phases 0-4) | one-time (per budget) | [migration-plan.md](migration-plan.md) |
| Balance Reconciliation & Validation | recurring (monthly?) | [balance-reconciliation.md](balance-reconciliation.md) |
| Budget Assignment Reconciliation | recurring (per budget period) | [ROADMAP.md](ROADMAP.md) |
| Goals Service (target amounts → `PUT /budgets`) | recurring (per budget period) | [future-tools.md](future-tools.md#goals-service-recreates-ynab-goals-on-top-of-lms-amount-only-budget-api) |
| Transaction Matching (dedupe manual vs Plaid) | recurring (on new-txn events) | [future-tools.md](future-tools.md) |
| Transfer Management (pair + group transfer legs) | recurring (on new-txn events) | [future-tools.md](future-tools.md) |
| Tracking-Category Audit | recurring (monthly?) | [future-tools.md](future-tools.md) |
| Account-Merging Helper | one-time (per merge) | [future-tools.md](future-tools.md) |
| Payee rename + auto-category rules import | one-time / occasional | [payee-rules-import-plan.md](payee-rules-import-plan.md) — needs Playwright UI automation, no API; different risk class from the rest, may not fit a background-service model at all |

Note several of these are "recurring" in the sense of needing to run periodically and diff
against fresh LM state — that's the shared shape LMAS would exist to serve. The migration and
account-merging modules are one-time-per-event and would just be regular CLI entry points
into the same codebase, not scheduled jobs.

## Open questions (not answered, just flagged)

- **Single local tool vs. hosted service?** Self-hosted CLI + cron is the obvious first step;
  "service" implies something more ambitious (a always-running process, maybe a web UI) that
  isn't justified yet.
- **State storage**: today each area uses its own local JSON (`sync_state.json` per
  `data/<slug>/<lm_account_id>/`). A unified service would want one coherent state layer
  across all modules — design TBD.
- **Scheduling model**: cron-driven polling vs. reacting to LM webhooks (does LM even have
  webhooks? unverified) vs. manual on-demand runs.
- **Would this ever become multi-user?** Only relevant if "expose migration as a feature"
  is taken seriously — i.e. other people running their own YNAB→LM migration through this
  service rather than forking the repo. Not assumed; flagged because it's the one thing that
  would change the risk profile significantly (auth, other people's tokens, support).

## Relationship to existing docs

This doc is a **speculative umbrella**, not an authority. When/if any individual module gets
scoped for real, its own design doc (as already exists for several in `future-tools.md`)
remains the source of truth for that module's design. [ROADMAP.md](ROADMAP.md) remains the
source of truth for priority/sequencing — this idea is not on the roadmap's critical path.
