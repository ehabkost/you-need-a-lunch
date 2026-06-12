# `notes` read-back mismatch on recurring-linked transactions (v2 API)

*Opened 2026-06-06 as "notes dropped on import"; root cause corrected 2026-06-12.*

> **TL;DR — there is no data loss.** Per-transaction `notes` are stored correctly on
> `POST`, updated correctly by `PUT`, and shown/edited in the UI. The **v2 API's `GET`
> returns the recurring item's inherited *display* note** in its `notes` field for
> recurring-linked transactions, which **masks** the stored per-transaction note (the v1
> API exposes both separately). Every earlier probe read the v2 `notes` field and
> wrongly concluded "notes dropped / PUT ignored." The only real impact was **phantom
> "would update" rows** in our importer's dedup; fixed by excluding `notes` from the
> comparison hash for recurring-linked txns (see [The fix](#the-fix-option-b)).

## Root cause (corrected)

Two true facts combine:

1. **A recurring-linked transaction inherits the item's payee + notes for display.**
   [Recurring Transactions](https://support.lunchmoney.app/finances/recurring-items/recurring-transactions):
   > "A recurring transaction will be in a final state and will inherit the same merchant
   > (payee) and description (notes) as defined in the recurring item that it is
   > associated with."

2. **The v2 API surfaces that *inherited display* value in its `notes` field**, not the
   stored per-transaction note. The v1 API keeps them separate. For the same recurring
   transaction (`2416404217`, a probe row whose stored note is `"note-jun"`):

   | | `notes` | `display_notes` | `recurring_description` |
   |---|---|---|---|
   | **v1** `GET /v1/transactions/{id}` | `"note-jun"` (stored, intact) | `null` | `null` |
   | **v2** `GET /v2/transactions/{id}` | `null` (= the display value) | — | — |

   The recurring item (`recurring_type: "suggested"`) has an empty description, so the
   inherited/display note is `null` — and v2 reports `null`, even though the real
   per-transaction note is `"note-jun"`.

This also dissolves the two things that looked like separate bugs:

- **"POST drops notes" — false.** The note we POST is stored; v2 just reads back the
  display value. (Also explains the old "payee survived but notes didn't": the item's
  merchant equals the payee we sent, while its description was empty.)
- **"PUT is silently ignored" — false.** Re-tested with a v1 read-back:
  ```
  before:  v1 notes = 'note-may'              recurring_id = 2905390
  PUT v2 {notes: "put-test-via-api"}
  after:   v1 notes = 'put-test-via-api'      ← the PUT DID write
           v2 notes = None                    ← v2 still shows the (null) display value
  ```
  The PUT wrote the stored note correctly; the v2 read-back only ever shows the display
  value, which is what made it look like a no-op.

LM also **auto-creates a "suggested" recurring item** from a clear payee+amount+cadence
pattern at import time
([Creating Recurring Items](https://support.lunchmoney.app/finances/recurring-items/creating-recurring-items));
this is why ordinary imported subscriptions get linked (and thus display-inherited) with
no `recurring_id` ever sent by us.

## Symptom (how it surfaced)

After importing the CAD budget, a routine dry-run reported **857 transactions "would
update (YNAB changed since last import)"** that never converged — re-running kept
re-reporting them.

```
── Transactions ──
  Would update    857  transaction(s) (YNAB changed since last import)
```

Breaking the 857 down by differing field, **490 were `notes`** where the LM snapshot had
`null` and YNAB had a memo — and **100% of those 490 carried a `recurring_id`**, none of
the notes-intact rows did. At the time this looked like recurring-linked data loss; it
was actually the dedup reading v2's display-`null` and comparing it to the memo we sent.
(The other buckets — `split_children` 345, `payee "[No Payee]"` 24 — are separate
comparison bugs, see [Related](#related-separate-comparison-bugs).)

## Why the earlier probes misled us

Both `scripts/probe_put_notes.py` and `scripts/probe_recurring_reinsert.py` read state
back through the **v2** API and therefore saw the display value:

- POSTing rows that form a recurring pattern → v2 read-back showed `notes: null` and a
  `recurring_id` (synchronously, for a clean monthly cadence; memo content is
  irrelevant — distinct vs identical memos behave identically).
- PUTting `notes`/`payee` on a recurring row → v2 read-back unchanged.

Every one of those observations is the v2 display-note artifact, not data loss. The
probes are kept (they correctly characterize v2's *read* behavior and the auto-detection)
but their original "data loss" conclusion was wrong.

## The fix (option (b))

The imported data is correct; only the **dedup comparison** was wrong, because it read
back v2 `notes` (display = `null` for recurring) and compared it to the memo we sent →
permanent mismatch → phantom "would update" forever.

Fix: **exclude `notes` from the LM comparison hash when the live transaction is linked to
a recurring item** (v2 can't report the real stored value there, so it's not comparable).
A `lm_recurring` flag is captured wherever we observe LM state and threaded through both
sides of the hash so they stay symmetric:

- `sinks.lm_fields_from_transaction` drops `notes` when `t.recurring_id is not None`
  (LM read-back / `--rebuild-index` side).
- `sinks.ScannedTxn` / `InsertResult.recurring_external` record recurring status at scan
  and at insert (from the POST response).
- `transactions.insert_lm_fields` / `compute_insert_lm_hash` take `lm_recurring=` and drop
  `notes` when set (YNAB-derived side).
- `sync_state.TxnEntry.lm_recurring` persists the flag; `set_txn(lm_recurring=None)`
  preserves it across update-applies.
- `build_transaction_update_plan` passes `entry.lm_recurring` into the insert-side hash.

Net: recurring-linked rows compare equal on every run → the 490 phantom updates vanish,
while a genuine YNAB memo change on a *non-recurring* row is still detected (its `notes`
stays in the hash). Non-recurring behavior is unchanged.

**Accepted trade-off:** a YNAB memo edit to a row that LM has linked to a recurring item
won't be re-detected/re-pushed (its `notes` is out of the hash). This is fine here — YNAB
is the source of truth, memos are written correctly on the initial `POST`, and we don't
edit memos in LM. Existing sync states need one `import --rebuild-index` to pick up the
`lm_recurring` flag and the notes-excluded hash (moot on a fresh start).

Tests: `tests/test_transaction_updates.py::test_recurring_readback_excludes_notes_from_lm_hash`
and `::test_recurring_txn_with_memo_no_phantom_update`.

## v2 read-back demonstration (for reference / optional LM note)

A clean v2-only view of the surprising read-back (the request sends real notes; the
response returns `null` + a `recurring_id` because v2 reports the inherited display note):

```
curl -s -X POST "https://api.lunchmoney.dev/v2/transactions" \
  -H "Authorization: Bearer $LUNCHMONEY_API_TOKEN" -H "Content-Type: application/json" \
  -d '{ "transactions": [
        {"date":"2026-01-15","amount":"-13.57","currency":"cad","payee":"Demo Co","notes":"note-jan","status":"unreviewed","manual_account_id":<ACCT>},
        {"date":"2026-02-15","amount":"-13.57","currency":"cad","payee":"Demo Co","notes":"note-feb","status":"unreviewed","manual_account_id":<ACCT>},
        {"date":"2026-03-15","amount":"-13.57","currency":"cad","payee":"Demo Co","notes":"note-mar","status":"unreviewed","manual_account_id":<ACCT>} ] }' | jq .
# → each row returns "notes": null, "recurring_id": <new id>
# Confirm the data is actually intact:  curl .../v1/transactions/<id>  → "notes":"note-jan", "display_notes":null
```

**Optional note to LM (not a data-loss bug):** v2 `GET /transactions` reports
`display_notes` in its `notes` field for recurring-linked transactions, with no separate
field for the stored per-transaction note (v1 exposes both). Consider exposing the stored
note (or a `display_notes`) in v2 so API consumers can tell them apart, and documenting
that `POST /transactions` triggers recurring auto-detection.

## Related (separate comparison bugs)

Independent of the notes issue, two of the original 857 buckets are false positives from
non-idempotent comparison in our own hash logic:

- **`split_children` (345):** when LM splits a parent, the children inherit the parent's
  `payee`/`notes` on read-back, but our re-derived children have them `None`.
  `transactions.insert_lm_fields` and `sinks.lm_fields_from_transaction` must normalize
  child `payee`/`notes` the same way on both sides.
- **`payee` "[No Payee]" (24):** LM stores the literal `"[No Payee]"` where we send
  `None`; the comparison must treat `"[No Payee]" == None`.

## Status

- Root cause corrected 2026-06-12: **no data loss**; v2 `notes` returns the inherited
  display note for recurring-linked txns. Writes (POST/PUT) and the UI are fine.
- Importer fixed (option (b)): `notes` excluded from the lm_hash for recurring-linked
  txns; phantom "would update" rows no longer generated. Tests added; full suite green.
- Probe scripts kept: `scripts/probe_put_notes.py`, `scripts/probe_recurring_reinsert.py`
  (both read v2 — interpret their `notes` output as the *display* value).
- Not reported to LM; an optional, narrowly-scoped API note is drafted above.
