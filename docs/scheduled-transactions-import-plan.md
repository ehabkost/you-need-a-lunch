# Scheduled Transactions Import — Implementation Plan (Phase 4)

This is the plan for migrating **YNAB scheduled transactions** to **Lunch Money recurring
items**. It complements:

- [migration-plan.md](migration-plan.md) §"Phase 4" — the phased overview (this doc is its detail).
- [transaction-importer-implementation.md](transaction-importer-implementation.md) — the code
  architecture (pure core + sink + DirSink) that this plan deliberately reuses.
- [transaction-import-plan.md](transaction-import-plan.md) — transfer/split classification rules
  that scheduled transactions share.

## 0. The blocking constraint — read this first

**Lunch Money's v2 API has no write endpoint for recurring items.** The OpenAPI spec
(`docs/lunchmoney-api-v2.json`) exposes only:

- `GET /recurring_items` — list all
- `GET /recurring_items/{id}` — fetch one

There is **no `POST`/`PUT`/`DELETE`**. **The v1 API is the same** — its documented recurring
endpoints (`GET /v1/recurring_expenses`, now deprecated in favour of `GET /v1/recurring_items`)
are **read-only too** (verified against the v1 docs at `alpha.lunchmoney.dev/v2/v1/...`; there is
no downloadable v1 OpenAPI spec file, only rendered docs pages). So switching API versions does
**not** unlock creation. Recurring items can only be created by the user inside the LM app, two
ways (confirmed via the LM knowledge base, *Creating Recurring Items*):

1. **Manual** — *Finances → Recurring → "Add new recurring item"* (merchant, billing date,
   category, cadence, fixed/fluctuating amount).
2. **Suggested (auto-detected)** — when transactions are imported (CSV/sync), LM detects a clear
   payee+amount+cadence pattern and creates a `status: suggested` item on the *Suggested
   Recurring* page, which the user must approve or reject. **Suggestions are not API-creatable
   either** — they emerge only from already-imported transaction history.

The official YNAB→LM migration guide says **nothing** about recurring items — there is no
first-class migration path. Consequently this phase **cannot push data via the API** the way
Phases 0–1 do. Its deliverable is a *human-actionable conversion artifact*, not an API import.

> **Scope/priority note:** This is the lowest-value, lowest-volume phase (53 / 7 / 0 items
> across `cad` / `brl` / `usd`). It should land **after** Phases 1–3 and is explicitly a
> "best-effort assist for manual re-entry," not an automated import. Revisit if LM ever ships a
> recurring-items write endpoint (see Open Question 1).

## 1. Source data shape

`data/<slug>/scheduled_transactions.json` — a flat JSON **array** (no wrapper object) of YNAB
scheduled-transaction objects. Observed profile (`data/cad`, 53 items):

| Frequency | Count | Transfers | With subtransactions | Deleted |
|---|---:|---:|---:|---:|
| `monthly` | 37 | — | — | — |
| `yearly` | 17 | — | — | — |
| `every4Weeks` | 2 | — | — | — |
| `everyOtherWeek` | 2 | — | — | — |
| `weekly` | 1 | — | — | — |
| `never` | 1 | — | — | — |
| **totals** | **53** | **4** | **1** | **1** |

Fields per item (same milliunits/sign conventions as regular transactions):
`id, date_first, date_next, frequency, amount, memo, flag_color, flag_name, account_id,
account_name, payee_id, payee_name, category_id, category_name, transfer_account_id, deleted,
subtransactions[]`. Subtransaction fields mirror regular split children
(`id, scheduled_transaction_id, amount, memo, payee_*, category_id, category_name,
transfer_account_id, deleted`).

Note the **distinct id space**: a scheduled transaction's `id` is *not* a regular transaction id
and never appears in `transactions.json`. Dedup keys here are independent of Phase 1.

## 2. Target model — LM `RecurringObject`

The read-only `RecurringObject` (in `lm_api_types_generated.py`) tells us the field shape we must
*emulate* in the manual-entry worklist:

- `transaction_criteria`: `granularity` (`day|week|month|year`), `quantity` (units between
  recurrences), `anchor_date`, `payee`, `amount` (decimal string, same `^-?\d+(\.\d{1,4})?$`
  pattern as transactions), `currency`, `manual_account_id` / `plaid_account_id`.
- `overrides`: `payee`, `notes`, `category_id` applied to matched transactions.
- `status`: `suggested` vs `reviewed` (only `reviewed` items match transactions).
- `source`: `manual | transaction | system`.

LM expresses cadence as **granularity + quantity** (e.g. "every 2 weeks" = `week` × 2). It has
**no native "twice a month" or one-off** cadence — those YNAB frequencies don't map cleanly
(§4).

## 3. Strategy — what this phase actually produces

Because there is no create API, the importer emits a **conversion + worklist**, and we lean on
LM's auto-detection for the rest. Recommended hybrid:

### 3a. Primary: a manual-entry worklist (the deliverable)

A pure-core converter turns each scheduled transaction into the LM recurring-item field set and
writes a **`recurring-worklist.md`** checklist (one row per item: merchant/payee, account,
category, cadence as "every N <unit>", anchor date, amount, fixed/fluctuating, notes) plus a
machine-readable **`recurring_items.json`** (the converted objects, for a future API import or
for diffing). The user re-creates them by hand in *Finances → Recurring → Add new recurring
item*. The checklist is ordered and groups by account so manual entry is fast.

### 3b. Secondary: rely on auto-detected suggestions

After Phase 1 imports the transaction *history*, LM will auto-suggest recurring items for any
payee+amount+cadence pattern it detects. The worklist (3a) doubles as a **review aid**: the user
can accept matching LM suggestions instead of hand-entering them, and use the worklist to catch
ones LM *didn't* suggest. Document this in the worklist header.

> Why not browser automation? Driving the LM UI to create items is out of scope: brittle,
> unversioned, and outside this tool's "API + local files" contract. Noted as a future option
> only (Open Question 2).

### 3c. Limit of auto-detection — why 3a is still needed

Auto-detection only fires on **imported transactions that already exhibit the pattern**. It will
miss:
- **Future-only** schedules whose `date_next` is in the future with little/no past history in the
  imported window (especially under `--since`).
- **Low-frequency** items (yearly, every-6-months) with only one occurrence in range.
- Items whose YNAB cadence LM can't express (§4) — never auto-suggested correctly.

So 3a (explicit conversion) is the reliable artifact; 3b is opportunistic cleanup.

## 4. Frequency mapping (YNAB → LM granularity × quantity)

| YNAB `frequency` | LM `granularity` | `quantity` | Notes |
|---|---|---:|---|
| `daily` | `day` | 1 | |
| `weekly` | `week` | 1 | |
| `everyOtherWeek` | `week` | 2 | |
| `every4Weeks` | `week` | 4 | Not identical to "monthly"; LM has no "4 weeks" preset but week×4 is exact |
| `monthly` | `month` | 1 | |
| `everyOtherMonth` | `month` | 2 | |
| `every3Months` | `month` | 3 | |
| `every4Months` | `month` | 4 | |
| `twiceAMonth` | — | — | **No clean LM cadence** (single granularity/quantity can't do 2×/month). Flag for manual handling: user creates two monthly items, or one with a chosen day. Not present in current data |
| `twiceAYear` | `month` | 6 | |
| `yearly` | `year` | 1 | |
| `everyOtherYear` | `year` | 2 | |
| `never` | — | — | One-off / no recurrence. **Not a recurring item.** Emit to a `non_recurring` bucket; suggest the user create a single scheduled/normal transaction instead. 1 present in `cad` |

`anchor_date` = `date_next` (the next expected occurrence; falls back to `date_first` if
`date_next` is null). `amount` uses the **same negate-then-÷1000** helper as transactions
(see [amount-conversion.md](amount-conversion.md)). YNAB scheduled amounts are **fixed**, so mark
the worklist item *fixed amount* (not fluctuating).

Frequencies present in current data are all mappable except the single `never`. The full table
above future-proofs against other budgets.

## 5. Field conversion

| LM recurring field | Source | Notes |
|---|---|---|
| `payee` / merchant | YNAB `payee_name` | Required by LM's create form |
| `amount` | `-ynab.amount / 1000`, 4dp string | Same helper as transactions ([amount-conversion.md](amount-conversion.md)); fixed amount |
| `currency` | sync_state `currency` | lowercase ISO 4217 |
| `granularity` + `quantity` | `frequency` per §4 | un-mappable → flagged bucket |
| `anchor_date` | `date_next` (fallback `date_first`) | ISO 8601 |
| account | `sync.account(account_id)` → `manual_account_id` / `plaid_account_id` | Phase 0 mapping; exactly one |
| `overrides.category_id` | `sync.lm_category_id(category_id)` | resolved like a transaction's category; null for uncategorized/income-internal |
| `overrides.notes` | YNAB `memo` | |

## 6. Decision table (special cases)

| # | Case | Action |
|---|------|--------|
| 1 | `deleted: true` | **Skip** (bucket `skipped_deleted`). Exported for completeness, never imported. |
| 2 | `frequency == "never"` | **Skip recurring**, emit to `non_recurring` bucket with a note: create a one-off transaction instead. |
| 3 | Un-mappable cadence (`twiceAMonth`) | Emit to `needs_manual_cadence` bucket with guidance (two items or pick a day). |
| 4 | **Transfer** (`transfer_account_id` set) | LM recurring items are single-account and have no transfer concept. Emit to `transfer_schedule` bucket: create a recurring item on the **migrated** leg's account categorized "Payment, Transfer"; document that the counterpart leg has no separate recurring representation. 4 present in `cad`. |
| 5 | **Split** (`category_name == "Split"`, non-empty `subtransactions`) | LM recurring items have **no split concept**. Two documented options in the worklist: (a) one recurring item at the parent amount with category "Incomplete Split"/null (preferred — matches the real charge), or (b) one recurring item *per* non-deleted subcategory. Default to (a); list the subcategory breakdown in the note. 1 present in `cad`. |
| 6 | Income (`Inflow: Ready to Assign`) | Map to the income category; LM recurring supports income. Mark worklist row as income. |
| 7 | Off-budget (source account `on_budget == false`) | Override category to "Tracking (off-budget)" (same rule as Phase 1), except transfers which stay "Payment, Transfer". |
| 8 | Account not migrated (skipped/excluded) | Pre-flight failure under `--on-missing abort`; under `skip`, drop to `skipped_missing`. |

These mirror the Phase-1 transaction rules so the converter can share the classification helpers.

## 7. Architecture — reuse the transaction-importer shape

Follow [transaction-importer-implementation.md](transaction-importer-implementation.md) exactly:
**pure core + sink + DirSink golden tests**, no inline I/O in the core.

```
lunchmoney/
  scheduled.py        # NEW — pure core: classify + YNAB→LM recurring conversion
  import.py           # phase_scheduled() wires core → output; CLI flags
tests/
  fixtures/           # tiny scheduled_transactions.json + expected worklist/json
  test_scheduled.py
```

```python
@dataclass(frozen=True)
class ScheduledImportOptions:
    since: date | None = None              # mirror --since (see §8)
    deferred_income_as: str | None = None  # reuse case-9 semantics for split subs

@dataclass
class ScheduledItem:
    ynab_id: str
    bucket: str                            # mappable | transfer_schedule | split |
                                           # needs_manual_cadence | non_recurring |
                                           # skipped_deleted | skipped_missing
    recurring: RecurringDraft | None       # converted LM fields; None for skipped buckets
    note: str = ""

def build_scheduled_plan(
    ynab_scheduled: list[dict[str, Any]], *, sync: SyncState,
    options: ScheduledImportOptions,
) -> ScheduledPlan: ...
```

All lookups go through the existing `SyncState` (accounts, categories, special cats, internal
cats) — no new sync_state fields are strictly required since nothing is written back via API
(see §9). `RecurringDraft` is a small local dataclass (not the read-only `RecurringObject`)
holding exactly the create-form fields from §5.

### Output sink

Reuse the `--to-dir` pattern. The sink writes:

```
<DIR>/
  recurring_items.json     # array of RecurringDraft dicts, sorted by (account, payee) — stable diffs
  recurring-worklist.md    # human checklist grouped by account; the deliverable
  summary.json             # bucket counts
```

There is **no `ApiSink` for creation** (no endpoint). The "apply" path is the human reading
`recurring-worklist.md`. An optional `ApiSink.verify()` may *read* `GET /recurring_items` after
the user has entered them and tick off matches by (account, payee, amount, cadence) — see §9.

## 8. `--since` interaction

A scheduled transaction is forward-looking (`date_next`), so `--since` (a *historical* cutoff)
mostly doesn't apply. Default: **include all** non-deleted schedules regardless of `--since`
(they describe the future, not history). Note in the worklist that under a partial-history
import, LM's auto-suggestions (3b) will be weaker, making the explicit worklist more important.

## 9. Dedup & re-run

No API writes ⇒ no `external_id`/`custom_metadata` to dedup on (recurring items have neither in
the schema). Re-run safety is therefore about the **worklist artifact**, not LM state:

- Regenerating the worklist is idempotent (deterministic sort, same content).
- For *verification* after manual entry: `GET /recurring_items`, match each draft against
  existing items by `(manual_account_id|plaid_account_id, payee, amount, granularity, quantity)`,
  and annotate the worklist with ✅ matched / ⬜ missing. This is read-only and re-runnable.
- Because matching is heuristic (no stable id bridge between YNAB schedule id and LM recurring
  id), verification is best-effort; document false-negative risk (e.g. user renamed payee).

## 10. Dry-run summary buckets

```
scheduled (recurring items):
  mappable (ready to enter):            N
  income:                               N
  transfers (recurring):                N
  splits (manual breakdown):            N
  needs manual cadence (twiceAMonth):   N
  non-recurring (frequency=never):      N
  skipped (deleted):                    N
  skipped (account not migrated):       N
```

## 11. Open questions

1. **Will LM add a recurring-items write endpoint?** Entire phase is gated on this. If/when it
   lands, add an `ApiSink` that POSTs `RecurringDraft`s and the worklist becomes a fallback.
   Until then, manual entry is the only path. *(Recheck the OpenAPI spec on each LM API update.)*
2. **Browser automation** to drive the "Add new recurring item" form — explicitly out of scope;
   record as a possible future tool only if manual volume proves painful.
3. **Auto-suggestion overlap** — after Phase 1, how many of these 53 does LM actually auto-detect?
   Measure once against the test account to decide how much manual entry the worklist truly saves.
4. **`twiceAMonth` / `never`** canonical guidance — confirm the recommended manual workaround with
   the user before finalizing the worklist copy (two monthly items? a single transaction?).
5. **Split recurring representation** (case 5) — confirm preference (single parent-amount item vs
   per-subcategory items) with the user; default is single parent-amount.
