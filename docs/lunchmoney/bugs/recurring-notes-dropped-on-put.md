# `notes` dropped on transactions linked to a recurring item (2026-06-06)

> **Resolution (2026-06-12): this is documented, intended LM behavior, not a bug.**
> A transaction linked to a recurring item **inherits the item's merchant (payee) and description
> (notes)**; and LM **auto-creates a "suggested" recurring item** from any clear payee + amount +
> cadence pattern at import time, then links the rows to it. Both are documented (see
> [Documented behavior](#documented-behavior-lm-docs)). The net effect on our import is real — notes
> on recurring-matched transactions are replaced by the (empty) item description — but it is by
> design. What remains worth raising with LM is narrower: the v2 API does this **silently** (200, no
> flag) and doesn't document that `POST /transactions` triggers recurring auto-detection. There is a
> documented opt-out (a "Don't link to recurring item" rule) we can use to preserve notes.
>
> The rest of this file is the original investigation, kept for the record.

## Symptom

After importing the CAD budget, a routine dry-run reported **857 transactions "would update (YNAB changed since last import)"** even though nothing had changed in YNAB. Running `import` again kept reporting the same updates — they never converged.

```
── Transactions ──
  Would update    857  transaction(s) (YNAB changed since last import)
```

## Investigation

The built-in update-diff (`-vv`, function `_log_update_diffs` in `lunchmoney/import.py`)
broke the 857 down by the field that differs between the LM snapshot and the freshly
re-derived payload:

| Differing field  | Count | Verdict |
|------------------|------:|---------|
| `notes`          | 490   | **Real loss** — LM has `null` where YNAB has a memo |
| `split_children` | 345   | False positive (comparison bug — see "Related" below) |
| `payee`          | 24    | False positive (LM normalization — see "Related") |
| `status`         | 6     | Mostly real (approved flag) |
| `amount`         | 1     | Edge |

Focusing on the 490 `notes` losses:

1. **The insert mapping was never broken.** `notes=txn.get("memo") or None` has existed in
   `transactions._make_insert` since the first Phase-1 commit (`fa5213e`). It works: 3,069 of
   3,080 *never-updated* transactions kept their notes.

2. **The loss correlates with a post-insert PUT.** Comparing `created_at` vs `updated_at` in the
   LM snapshot (`data/cad/101330/transactions.json`):

   ```
   MISSING notes: updated-after-insert=316  not-updated=174
   HAVE notes:    updated-after-insert= 11  not-updated=3069
   ```

3. **Fish history pinned the run** that bumped `updated_at` on the affected txns
   (`~/.local/share/fish/fish_history`):

   ```
   2026-06-04 04:13:46Z  import --data data/cad import --update-index   # wiped ynab_hash baseline
   2026-06-04 04:15:12Z  import --data data/cad import --apply          # PUT per txn, 04:15:20…
   ```

4. **The decisive correlation:** every missing-notes txn carries a `recurring_id`; none of the
   notes-intact txns do. **100% clean split:**

   ```
   MISSING notes: recurring=490  non-recurring=0
   HAVE notes:    recurring=0    non-recurring=3080
   ```

## Reproducer (controlled live probe)

`scripts/probe_put_notes.py` runs `GET → PUT{notes} → GET → PUT{payee, no notes} → GET → restore`
against `LUNCHMONEY_API_TOKEN` (test account via `./test-run.sh`).

- **Control txn `2410817250`** (no `recurring_id`):
  ```
  Step 1 PUT {notes: "probe-set"}      -> notes persisted   ✓
  Step 2 PUT {payee} (no notes key)    -> notes preserved   ✓  (PUT is a MERGE)
  ```
  PUT works exactly as expected and is a partial-update merge.

- **Recurring txn `2410817400`** (`recurring_id: 2869339`, banner "We think this is a recurring item"):
  ```
  Step 1 PUT {notes: "probe-set"}      -> notes still None   ✗  (silently ignored)
  Step 2 PUT {payee: "probe-payee"}    -> payee unchanged    ✗  (silently ignored)
  ```
  Both PUTs returned success and bumped `updated_at`, but **no field actually changed**.

## Root cause

**Lunch Money does not persist the `notes` field on a transaction once it is matched to a
recurring item, and it silently ignores subsequent `PUT` field updates on such transactions**
(observed for `notes` and `payee`; the request still 200s and bumps `updated_at`).

Sequence that produced the symptom:

1. `POST /transactions` creates the txn → LM's recurring matcher attaches a `recurring_id` and
   **drops our `notes`** (the `payee` happened to survive because it equals the recurring item's
   merchant name).
2. At insert, our sync state stored `lm_hash` computed from *what we sent* (with notes), so it
   already disagreed with LM's actual stored state (null notes). `--update-index` later recomputed
   `lm_hash` from the real LM snapshot (null notes).
3. `--apply` compared the YNAB-derived payload (with notes) against the stored `lm_hash` (null
   notes), flagged all 490 as "changed," and issued a `PUT` per txn — a **no-op** that only bumped
   `updated_at`. This is why the app's **Change History shows only "Transaction created via API"**
   with no update entry (LM's UI change-log also does not record API PUTs).
4. Because PUT can never write notes onto a recurring-locked txn, the `lm_hash` never converges and
   the dry-run re-flags the same 490 on every run.

## Notes on LM behavior discovered along the way

- **The in-app "Change History" panel does not record API `PUT` updates** — only creation (and
  presumably manual UI edits). It cannot be used to audit what the API changed.
- `updated_at` is bumped even when a PUT changes nothing.
- `PUT /transactions/{id}` is a **merge** (partial update), not a full replace: fields omitted from
  the body are preserved.

## Impact on the importer

- 490 phantom "would update" rows that never converge (CAD budget; scales with how many imported
  txns LM auto-matches to recurring items — bank fees, transfers, subscriptions, etc.).
- The lost notes are **not recoverable via the API** while the txn stays matched to a recurring
  item.

## Recommended handling (our side)

1. **Preserve notes (opt-out):** optionally set the documented **"Don't link to recurring item"**
   rule (see [Documented behavior](#documented-behavior-lm-docs)) on the LM account before importing,
   so transactions are not auto-linked and keep their `notes`. Trade-off: the user loses LM's
   automatic recurring detection for imported data; make this a choice, not a default.
2. **Stop re-flagging:** in `build_transaction_update_plan`, when the live txn has a `recurring_id`,
   exclude `notes` (and any other item-inherited field) from the update comparison / hash, or skip
   the update entirely so these drop out of the plan. Needed regardless of the opt-out, for any
   transactions LM matches to a pre-existing recurring item.
3. **Set expectations:** document that, by LM design, notes on recurring-linked transactions are
   replaced by the recurring item's (often empty) description and cannot be repaired by re-running.
4. Consider reporting to LM support (now scoped as a doc/UX gap, not a data-loss bug): `POST` should
   signal when submitted `notes` are overridden by recurring inheritance instead of a silent 200,
   and the API docs should state that inserts trigger recurring auto-detection + the opt-out.

## Update (2026-06-12): the drop is synchronous at POST, and memo-independent

A second round of controlled live probes (`scripts/probe_recurring_reinsert.py`, test account)
refined the root cause. The original write-up assumed the recurring matcher runs *asynchronously*
after insert. That is only one of two paths — the matcher also fires **synchronously inside the
`POST /transactions` request**:

1. **Reinsert an existing recurring group (auto-match, no `recurring_id` sent).** Deleted all 5
   members of recurring item `2869334` (CRAVE) and re-POSTed them in one request. Immediate
   read-back: `recurring_id=null`, **notes kept**. The matcher had *not* run yet — confirming the
   async path. (These CRAVE rows had old, sparse, irregular dates: 2020-07 … 2021-05.)

2. **Insert with `recurring_id` set explicitly.** Same operation on item `2869473` (NYT Games) but
   sending `recurring_id` on each insert: all 5 came back **matched and with `notes=null`** — the
   drop happened inside the single POST.

3. **Seed a brand-new pattern (never-seen payee + amount + clean monthly cadence).** POSTed 6
   monthly rows of a fictional payee in one request. LM **created a new recurring item on the spot**
   (`recurring_id` higher than any existing) and stored all 6 with `notes=null` — synchronously, in
   the POST response. So a normal YNAB import (e.g. a year of a monthly subscription) trips this
   without any `recurring_id` being sent and without waiting for a background job.

4. **Memo content is irrelevant.** Two seed runs, one with a *distinct* memo per row and one with an
   *identical* memo on every row, both matched and both dropped notes 6/6. The detector keys on
   **payee + amount + regular cadence**, never on `notes`. (This rules out an early hypothesis that
   unique memos were suppressing detection — they are not.)

Net: notes are lost the moment LM associates a transaction with a recurring item; that association
can occur *during* the POST when the inserted rows themselves form a recurring pattern. In all cases
`skipped_duplicates` was empty — LM does not dedup multiple rows matching one recurring item, and the
POST returns 200 with no warning that notes were discarded.

## Documented behavior (LM docs)

After the probes above, the LM support docs were checked — **this behavior is documented and
intended.** Two separate pages cover the two halves:

**1. A recurring transaction inherits the item's payee + notes.**
[Recurring Transactions](https://support.lunchmoney.app/finances/recurring-items/recurring-transactions)
(highlighted info box):

> "Once a transaction is linked to a recurring item, it cannot be further split or grouped. **A
> recurring transaction will be in a final state and will inherit the same merchant (payee) and
> description (notes) as defined in the recurring item** that it is associated with."

This is the real mechanism: notes aren't "discarded," they are **overwritten by the recurring
item's description** (empty for an auto-created item → `null`). It also explains the old CAD puzzle
of why `payee` survived but `notes` didn't — the item's merchant equals the payee we sent (no-op),
while its description was empty (wipes notes). It also makes the PUT no-op *expected*: a recurring
transaction is in a "final state" and re-derives payee/notes from the item, so a `PUT {notes}` can't
stick.

**2. LM auto-creates a "suggested" recurring item from a clear pattern.**
[Creating Recurring Items](https://support.lunchmoney.app/finances/recurring-items/creating-recurring-items):

> "If you use bank syncing or CSV imports, Lunch Money will automatically detect recurring
> transactions if there is a **clear pattern of payee and amount that repeat over a regular
> cadence**. If a recurring pattern is detected, a **suggested recurring item will be created and
> those transactions will be linked to it.**"

This is exactly the seed result (new item from payee + amount + monthly cadence). Note the gap: the
docs say *"bank syncing or CSV imports"* and do **not** mention the **v2 API** `POST /transactions`,
which triggers the same detection.

**3. Documented opt-out that preserves notes.**
[FAQ](https://support.lunchmoney.app/finances/recurring-items/faq) describes a **"Don't link to
recurring item"** rule (condition: *Matches day → Day is between 1 & 31*, i.e. all transactions;
action: *Don't link to recurring item*) that "prevent[s] the automatic creation of new suggested
recurring items." Setting this rule on the account **before importing** stops auto-linking, so the
submitted `notes` are kept.

What is still arguably worth raising with LM (vs. documented design): `POST /transactions` applies
all of this **silently** — `200`, empty `skipped_duplicates`, no flag that the submitted `notes`
were overridden by inheritance — and the API docs don't state that inserts trigger recurring
auto-detection or how to opt out.

## Related (separate comparison bugs surfaced by the same dry-run)

Independent of the recurring-notes issue, two of the 857 buckets are false positives caused by
non-idempotent comparison in our own hash logic — fix these so the dry-run is trustworthy:

- **`split_children` (345):** when LM splits a parent, the children inherit the parent's `payee`
  and `notes` on read-back, but our re-derived children have them `None`. `insert_lm_fields`
  (`transactions.py`) and `lm_fields_from_transaction` (`sinks.py`) must normalize child
  `payee`/`notes` the same way on both sides.
- **`payee` "[No Payee]" (24):** LM stores the literal string `"[No Payee]"` where we send `None`.
  The comparison must treat `"[No Payee]" == None`.

## Status

- Root cause confirmed via live probe on the test account (2026-06-06); refined to "synchronous,
  memo-independent" on 2026-06-12; then confirmed against LM docs as **documented, intended
  behavior** (recurring inheritance + suggested-recurring auto-detection). Reclassified from bug to
  documented behavior; not reported to LM (a narrower doc/UX-signal note is optional — see the draft
  below).
- Importer not yet changed; phantom updates are harmless (PUTs are no-ops) but noisy and block a
  clean "nothing to do" dry-run. The documented **"Don't link to recurring item"** rule is an
  opt-out that preserves notes (see Recommended handling).
- Probe scripts kept at `scripts/probe_put_notes.py` (PUT behaviour) and
  `scripts/probe_recurring_reinsert.py` (delete/reinsert + seed). The single-curl reproducer is
  inlined in the draft below (the standalone `repro_recurring_notes_drop.sh`/`.output.txt` were
  removed as redundant).

## Single-curl demonstration + optional note to LM support

This is documented behavior (recurring inheritance + suggested-recurring auto-detection), so the
write-up below is framed as a **demonstration of the feature** plus an optional, narrowly-scoped
note to LM — not a data-loss bug report.

**Optional note to LM (doc/UX gap, not a bug):** v2 `POST /transactions` triggers recurring
auto-detection and overrides the submitted `notes` (and `payee`) via recurring inheritance, but does
so **silently** — `200`, empty `skipped_duplicates`, no flag that the values were overridden — and
the API docs don't mention that inserts trigger detection or how to opt out. Suggested: signal the
override in the response, and document the behavior + the "Don't link to recurring item" opt-out for
the API path.

**Demonstration (one curl).** Create a few transactions with the same payee, account, and amount,
one per month on the same day of the month, each with a different value for `notes`. They form a
clear recurring pattern, so LM creates a suggested recurring item and links them; per the documented
inheritance rule each linked row takes the item's (empty) description, so `notes` comes back `null`: 

```
ehabkost@elnuevo:~/Projects/you-need-a-lunch$ curl -s -X POST "https://api.lunchmoney.dev/v2/transactions" \
  -H "Authorization: Bearer $LUNCHMONEY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "transactions": [
      {"date": "2026-01-15", "amount": "-13.57", "currency": "cad", "payee": "Bug Reproducer Co", "notes": "note-jan", "status": "unreviewed", "manual_account_id": 342356},
      {"date": "2026-02-15", "amount": "-13.57", "currency": "cad", "payee": "Bug Reproducer Co", "notes": "note-feb", "status": "unreviewed", "manual_account_id": 342356},
      {"date": "2026-03-15", "amount": "-13.57", "currency": "cad", "payee": "Bug Reproducer Co", "notes": "note-mar", "status": "unreviewed", "manual_account_id": 342356}
    ]
  }' | jq .
{
  "transactions": [
    {
      "id": 2416405150,
      "date": "2026-01-15",
      "amount": "-13.5700",
      "currency": "cad",
      "to_base": -13.57,
      "recurring_id": 2905399,
      "payee": "Bug Reproducer Co",
      "original_name": "Bug Reproducer Co",
      "category_id": null,
      "notes": null,
      "status": "unreviewed",
      "is_pending": false,
      "created_at": "2026-06-12T02:15:16.606Z",
      "updated_at": "2026-06-12T02:15:16.606Z",
      "is_split_parent": false,
      "split_parent_id": null,
      "is_group_parent": false,
      "group_parent_id": null,
      "manual_account_id": 342356,
      "plaid_account_id": null,
      "tag_ids": [],
      "source": "api",
      "external_id": null,
      "plaid_metadata": null,
      "custom_metadata": null,
      "files": []
    },
    {
      "id": 2416405151,
      "date": "2026-02-15",
      "amount": "-13.5700",
      "currency": "cad",
      "to_base": -13.57,
      "recurring_id": 2905399,
      "payee": "Bug Reproducer Co",
      "original_name": "Bug Reproducer Co",
      "category_id": null,
      "notes": null,
      "status": "unreviewed",
      "is_pending": false,
      "created_at": "2026-06-12T02:15:16.606Z",
      "updated_at": "2026-06-12T02:15:16.606Z",
      "is_split_parent": false,
      "split_parent_id": null,
      "is_group_parent": false,
      "group_parent_id": null,
      "manual_account_id": 342356,
      "plaid_account_id": null,
      "tag_ids": [],
      "source": "api",
      "external_id": null,
      "plaid_metadata": null,
      "custom_metadata": null,
      "files": []
    },
    {
      "id": 2416405152,
      "date": "2026-03-15",
      "amount": "-13.5700",
      "currency": "cad",
      "to_base": -13.57,
      "recurring_id": 2905399,
      "payee": "Bug Reproducer Co",
      "original_name": "Bug Reproducer Co",
      "category_id": null,
      "notes": null,
      "status": "unreviewed",
      "is_pending": false,
      "created_at": "2026-06-12T02:15:16.606Z",
      "updated_at": "2026-06-12T02:15:16.606Z",
      "is_split_parent": false,
      "split_parent_id": null,
      "is_group_parent": false,
      "group_parent_id": null,
      "manual_account_id": 342356,
      "plaid_account_id": null,
      "tag_ids": [],
      "source": "api",
      "external_id": null,
      "plaid_metadata": null,
      "custom_metadata": null,
      "files": []
    }
  ],
  "skipped_duplicates": []
}
ehabkost@elnuevo:~/Projects/you-need-a-lunch$
```

**Result:** every transaction comes back with `notes: null` and a `recurring_id` pointing at the
newly-created (suggested) recurring item. This matches the documented inheritance rule — the rows
took the item's empty description. The `200` response and empty `skipped_duplicates` give no signal
that the submitted `notes` were overridden.

**Notes / corroborating observations**

- The override is tied to recurring *linking*, not to `POST` itself: rows that aren't linked at
  insert keep their notes (e.g. reinserting rows with old, irregular dates that don't form a clean
  cadence). The override also happens when an explicit `recurring_id` is supplied on insert.
- `notes` content does not affect detection (distinct vs identical memos behave identically) — the
  detector keys on payee + amount + cadence, consistent with the docs.
- `PUT /transactions/{id}` with a `notes` (or `payee`) body on a recurring-linked transaction is a
  no-op (returns 200, bumps `updated_at`, persists no change) — expected, since a recurring
  transaction is in a "final state" and re-derives those fields from the item.

**To preserve notes on import:** set the documented "Don't link to recurring item" rule on the
account first (FAQ), which suppresses auto-linking so the submitted `notes` stick.
