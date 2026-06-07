# Lunch Money API Bug Reports

## Bug 1: POST /transactions returns 500 — `original_name` missing DB column (2026-06-02)

### Symptom

`POST /transactions` returns HTTP 500 with:

```json
{
  "message": "Undefined binding(s) detected when compiling SELECT. Undefined column(s): [original_name] query: select * from \"recurring_expenses\" where \"account_id\" = ? and \"original_name\" = ? and \"amount\" = ? and \"currency\" = ?",
  "errors": []
}
```

### Root cause

This is a **server-side LM bug**. When inserting a transaction, LM's recurring-expense matching logic runs a SQL query that references `original_name` as a column on the `recurring_expenses` table. That column does not exist in the DB schema, causing the 500.

Our importer does **not** send `original_name` in the request body (we use `model_dump(mode="json", exclude_none=True)` and never set the field). The bug is triggered regardless — LM always runs this query when inserting into an account that has recurring items.

### Reproducer

```sh
curl -s -X POST https://api.lunchmoney.dev/v2/transactions \
  -H "Authorization: Bearer $LUNCHMONEY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transactions":[{"date":"2024-01-01","amount":"10.00","payee":"Test","manual_account_id":341928}]}'
```

### Workaround

None on our side — this is entirely in LM's server. We cannot work around it by changing request shape. Need LM to add the missing column or remove it from the query.

### Status

- **Likely transient.** Re-running all 25 batches (12,026 txns total) the next day completed without error. First 8 batches skipped as duplicates (already inserted in the failed run); remaining 17 batches inserted successfully.
- Not yet reported to LM support — may have been a momentary server issue.
- Not currently blocking.

---

## Bug 2: `notes` silently dropped on transactions matched to a recurring item (2026-06-06)

### Symptom

After importing the CAD budget, a routine dry-run reported **857 transactions "would update (YNAB changed since last import)"** even though nothing had changed in YNAB. Running `import` again kept reporting the same updates — they never converged.

```
── Transactions ──
  Would update    857  transaction(s) (YNAB changed since last import)
```

### Investigation

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

### Reproducer (controlled live probe)

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

### Root cause

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

### Notes on LM behavior discovered along the way

- **The in-app "Change History" panel does not record API `PUT` updates** — only creation (and
  presumably manual UI edits). It cannot be used to audit what the API changed.
- `updated_at` is bumped even when a PUT changes nothing.
- `PUT /transactions/{id}` is a **merge** (partial update), not a full replace: fields omitted from
  the body are preserved.

### Impact on the importer

- 490 phantom "would update" rows that never converge (CAD budget; scales with how many imported
  txns LM auto-matches to recurring items — bank fees, transfers, subscriptions, etc.).
- The lost notes are **not recoverable via the API** while the txn stays matched to a recurring
  item.

### Recommended handling (our side)

1. **Stop re-flagging:** in `build_transaction_update_plan`, when the live txn has a `recurring_id`,
   exclude `notes` (and any other LM-locked field) from the update comparison / hash, or skip the
   update entirely so these drop out of the plan.
2. **Set expectations:** document that notes on recurring-matched transactions can be lost on import
   and cannot be repaired by re-running.
3. Consider reporting to LM support: POST should honor the submitted `notes` even when it matches a
   recurring item; PUT should not silently ignore field updates (or should return an error/flag).

### Related (separate comparison bugs surfaced by the same dry-run)

Independent of the recurring-notes issue, two of the 857 buckets are false positives caused by
non-idempotent comparison in our own hash logic — fix these so the dry-run is trustworthy:

- **`split_children` (345):** when LM splits a parent, the children inherit the parent's `payee`
  and `notes` on read-back, but our re-derived children have them `None`. `insert_lm_fields`
  (`transactions.py`) and `lm_fields_from_transaction` (`sinks.py`) must normalize child
  `payee`/`notes` the same way on both sides.
- **`payee` "[No Payee]" (24):** LM stores the literal string `"[No Payee]"` where we send `None`.
  The comparison must treat `"[No Payee]" == None`.

### Status

- Root cause confirmed via live probe on the test account (2026-06-06). Not yet reported to LM.
- Importer not yet changed; phantom updates are harmless (PUTs are no-ops) but noisy and block a
  clean "nothing to do" dry-run.
- Probe script kept at `scripts/probe_put_notes.py`.
