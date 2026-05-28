# Multi-Currency / Multi-Budget Strategy

How cross-currency holdings have been tracked in YNAB, and how the importer should translate them into Lunch Money.

This document is more specific than [multi-budget.md](multi-budget.md) (which covers generic per-account exclusion and one-budget-to-many-LM-accounts mechanics). When the two disagree, prefer this one for currency-related decisions.

## Current YNAB-side reality

There is **one** YNAB budget, denominated in CAD. It contains two kinds of accounts:

1. **Real CAD bank accounts** — full per-transaction history, amounts in CAD milliunits. Behaves like a normal YNAB account.
2. **BRL-proxy accounts** — represent real BRL bank accounts that physically exist in Brazil, tracked in the CAD budget as:
   - **Off-budget (tracking accounts)**. Their balance changes do *not* flow into the CAD budget's Ready-to-Assign, so the CAD-side category/budget logic is not contaminated by BRL valuation moves.
   - Primarily a series of **manual balance-adjustment snapshots**, each recording the CAD-equivalent of the real BRL balance at that moment, at whatever FX rate the user picked that day.
   - Some non-balance-adjustment transactions also exist on these accounts (categorised entries, transfers), but they are **planning approximations of real-world BRL activity, not authoritative records**, and have no meaningful category assignment. Losing them is acceptable.
   - Real CAD↔BRL money movements (e.g. CAD chequing → convert → BRL bank) **are recorded as in-YNAB transfers** between a CAD account and the BRL-proxy account. The CAD leg is authoritative (real money left a real CAD account); the BRL leg is a CAD-equivalent placeholder.

There is **no separate BRL YNAB budget**. The "second budget" scenario described in `multi-budget.md` is historical / hypothetical and does not currently apply.

### What this means in practice

- The BRL-proxy accounts hold no data worth migrating *as-is*: the snapshots are CAD-valued, the non-snapshot transactions are planning approximations, and none of it affects the CAD budget's categories.
- The only piece of authoritative data tied to the BRL-proxy accounts is the **CAD-side leg of CAD↔BRL transfers** — real outflows from real CAD accounts. Those must not be lost.

## Desired Lunch Money end state

One LM account per real-world bank account, each using its native currency:

- CAD banks → LM account with `currency: CAD` and full transaction history.
- BRL banks → LM account with `currency: BRL`, holding the actual real-world BRL balance.

LM handles cross-currency aggregation natively for net-worth views, so there is no need for shadow / proxy accounts on the LM side.

## The translation problem (BRL-proxy → BRL LM account)

The CAD-equivalent snapshot history in YNAB cannot be transferred directly to a BRL-currency LM account: the numbers are in the wrong currency and the FX rates that produced them are not recorded. Three options, ranked:

### Option A — Skip & manually seed (chosen)

- The importer **excludes the BRL-proxy YNAB accounts** entirely from the account+transaction import.
- The user will create the corresponding BRL LM accounts manually during the post-import "sort out the mess" stage (see below).
- Nothing meaningful is lost on the YNAB-export side: snapshots were CAD-valued, planning transactions were approximations, and the proxy accounts were off-budget so they had no category impact.
- The CAD-side legs of CAD↔BRL transfers are **preserved** via the existing transfer-to-excluded-account rule (see next section).

### Option B — Back-convert snapshots

- Convert each CAD-equivalent snapshot back to BRL using historical FX rates the user supplies (or fetches from an external rate API per snapshot date).
- Creates a BRL-currency LM account containing one balance-adjustment entry per snapshot date, in BRL.
- Preserves the snapshot history in LM, at the cost of FX rate data and per-snapshot user confirmation.
- Not chosen — the snapshot history isn't valuable enough to justify the FX reconstruction.

### Option C — Import as CAD shadow accounts (rejected)

- Import the BRL-proxy accounts as-is into LM with `currency: CAD`.
- Contradicts the desired end state. Not chosen.

## CAD↔BRL transfers: what happens to the CAD-side leg

Because the user records cross-currency money movement as in-YNAB transfers to/from the BRL-proxy account, excluding the BRL proxy creates "orphaned" transfer legs on the CAD side. This is **already handled** by the transfer strategy in [transaction-import-plan.md](transaction-import-plan.md):

> If the destination account was not migrated (excluded, or Plaid read-only): Import the outflow side as a regular transaction with null category and payee = original "Transfer : X" string.

Result in LM: each historical CAD→BRL transfer becomes a regular outflow on the CAD account, with payee preserved as `"Transfer : <BRL-proxy-name>"` and `custom_metadata.ynab_id` retained. The user keeps the historical record of "$X CAD left chequing on date Y, destined for Brazil" — exactly the authoritative piece worth preserving — without dragging in the BRL-side placeholder.

The reverse direction (BRL→CAD inflow to a CAD account) works the same way: the CAD-side inflow leg is imported as a regular transaction with the `"Transfer : <BRL-proxy-name>"` payee.

## Post-import "sort out the mess" stage

The user's plan is to complete the YNAB→LM data import end-to-end first, then do manual cleanup work on the LM side. This stage includes:

1. **Creating BRL LM accounts** manually, in BRL, seeded from the real bank app / statement balances (not from YNAB).
2. Potentially **merging or splitting LM accounts** where the import created a structure that doesn't match the desired end state.
3. Linking newly-created BRL LM accounts with anything from YNAB that should retroactively connect to them (e.g. re-labelling the orphaned CAD-side transfer legs once the BRL destination exists in LM).

This means the importer does **not** need to solve manual-seeding or BRL account creation itself. It just needs to:
- Exclude the BRL-proxy accounts cleanly.
- Preserve enough metadata on the orphaned CAD-side transfer legs that the user (or a future tool) can find and re-pair them later.
- Stay re-run-safe so it doesn't fight the manual cleanup.

## The "import everything and accept duplication" scenario — why it's moot

The original framing of this question imagined a world where both a CAD budget and a separate BRL budget existed, both tracking the same BRL banks. Importing both would duplicate the BRL balance in LM (once as a CAD-equivalent shadow, once natively).

Since the separate BRL budget no longer exists, **this duplication scenario does not currently apply**. The only duplication risk is the BRL-proxy accounts themselves, which Option A eliminates by exclusion.

If a separate BRL budget is ever reintroduced, this concern returns and the new budget's import will need to reconcile against the already-existing LM BRL accounts. See the deferred questions below.

## Required importer features

To support the chosen strategy on v1:

1. **Per-account exclude list** (already noted in `multi-budget.md`): config-driven list of YNAB account IDs (or names) to skip during account+transaction import. Not just a low-priority hint — it is **required** for the BRL-proxy use case.
2. The exclude list lives in the per-import-pair config (see [transaction-import-plan.md](transaction-import-plan.md) gap #16).
3. Excluded accounts must be reported in the dry-run summary as a distinct bucket (`excluded by config: N`).
4. Orphaned transfer legs (CAD-side of CAD↔BRL transfers, with the BRL side excluded) should be reported in the dry-run summary as a distinct bucket too (`transfers (destination excluded): N`) so the user can verify the count matches expectations before applying.

What is **not** required for v1:
- Per-account currency override for the import path — there's only one currency-of-record (CAD) for the accounts being imported.
- FX rate handling, historical or current.
- Cross-budget transfer pairing.
- Manual BRL account seeding tooling — handled by the user in the post-import stage.

## Open questions (research needed before finalising the plan)

These are tracked here rather than in the transaction-import-plan gap list because they're specific to the multi-currency / multi-budget strategy:

### Q1. How does LM's account-merging feature work?

The post-import "sort out the mess" stage will likely involve merging or restructuring LM accounts (e.g. if the user later decides to consolidate, or if the manual BRL seeding ends up needing to absorb data from an imported account). Need to investigate:
- Does LM support merging two manual accounts? Two Plaid accounts? Manual into Plaid (or vice versa)?
- What happens to `external_id`, `custom_metadata`, transaction history, and category mappings on merge?
- Does the surviving account's sync_state remain valid for re-runs of the importer? If not, the importer needs a way to detect post-merge state and recover.

If LM's merge feature has serious limitations, **a "merge helper" tool may need to be written** (see [future-tools.md](future-tools.md) — should be added there as a planned post-migration tool).

### Q2. How does LM handle category balances in a multi-currency scenario?

Relevant if/when multiple YNAB budgets feed one LM account (see next section). Need to confirm:
- When transactions in two different currencies share the same LM category, does LM compute a per-currency total, a base-currency total via FX, or both?
- Are category budget amounts denominated in the LM account's base currency only, or per-currency?
- Are there practical limits or quirks (precision, FX rate source, historical FX) that would make multi-currency category balances unreliable?

The user is comfortable with **not** tracking BRL category balances precisely, since BRL transactions to be imported are mostly historical. If LM's multi-currency category support is weak, the per-budget-prefixed categories approach below is sufficient.

## Deferred: multiple YNAB budgets → single LM account

Not currently in scope. Captured here so the design doesn't paint itself into a corner.

Open questions to revisit if a second YNAB budget (e.g. a real BRL budget) is ever added:

1. **Matching against existing LM accounts**. If the BRL banks already exist in LM (created by the user during the sort-out-the-mess stage), the second budget's import must match against them rather than re-creating. This is the **only** "match against existing LM entities" case that survives the create-from-scratch strategy shift; it will need explicit config — a map of `(ynab_account_id_in_new_budget → existing_lm_account_id)` — rather than name-based heuristics.

2. **Category handling across budgets**. Leading hypothesis: **keep categories separate per source budget** so balances in different currencies don't get conflated in a single LM category. Concretely:
   - **Prefix by budget**: `"CAD: Groceries"` vs. `"BRL: Mercado"`. Preserves separation, bloats the list. Simple to implement.
   - **One LM category group per source budget**: cleaner separation than prefixing, leverages LM's native grouping. Probably the preferred direction.
   - **Merge by name**: explicitly *not* preferred, because it would require LM to handle multi-currency category balances correctly (see Q2 above), and would mean BRL spending inflates or deflates CAD category totals via implicit FX.
   - Confirming the choice depends on the answer to Q2. If LM handles multi-currency categories cleanly, merging becomes an option; if not, separation is the only safe choice.
   - Given that BRL transactions are mostly historical, *precise* BRL category balances are not required — so even a lossy approach (e.g. don't bother trying to budget against BRL-imported categories) is acceptable.

3. **Cross-budget transfers**. YNAB has no cross-budget or cross-currency transfer concept (see `transaction-import-plan.md` gap #12). Cross-currency money movement appears as two unrelated transactions in two budgets. If both are imported into one LM account, they will surface as two unpaired transactions. Reconciliation is manual (Transfer Management Tool, see [future-tools.md](future-tools.md)).

4. **Sync state organisation**. Per-(LM-account) sync state may need to be per-(LM-account, YNAB-budget) to keep mappings clean when one LM account is fed by multiple budgets. Worth revisiting then.

## Summary

- Today: one CAD YNAB budget; BRL banks tracked as off-budget CAD-valued snapshot accounts inside it; real CAD↔BRL transfers recorded as in-YNAB transfers to those proxies.
- v1 import strategy: **Option A** — exclude BRL-proxy accounts, import the rest of the CAD budget normally. The existing transfer-to-excluded-account rule preserves the authoritative CAD-side leg of CAD↔BRL transfers. The user manually creates BRL LM accounts during a post-import "sort out the mess" stage.
- Required v1 importer features: per-account exclude list (config-driven), excluded-account and excluded-destination dry-run buckets.
- Two open research items: LM account-merging behaviour, and LM multi-currency category-balance semantics.
- Future-proofing: leave room for a second-budget import that matches against existing LM accounts; defer the per-budget category-separation choice until LM's multi-currency behaviour is understood.
