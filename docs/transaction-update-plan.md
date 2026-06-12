# Transaction Update Plan

This is a complete implementation plan for an agent to implement transaction update support:
detecting YNAB changes to already-imported transactions and propagating them to Lunch Money,
while preserving user edits made directly in LM (one-way sync).

Companion docs:
- [transaction-importer-implementation.md](transaction-importer-implementation.md) — Phase 1 insert architecture (splits, sinks, sync_state)
- [api-reference.md](api-reference.md) — YNAB and LM field reference

> **⚠ Superseded in part — read [Revision 2](#revision-2-2026-06-12-drop-lm_hash-split-sync-from-reconcile) first.**
> The original design below uses **two hashes** (`ynab_hash` + `lm_hash`) and recomputes
> `lm_hash` from the LM read-back at `--rebuild-index`. That read-back path is exactly what
> produced the never-converging "phantom update" bug (see
> [bugs/recurring-notes-dropped-on-put.md](lunchmoney/bugs/recurring-notes-dropped-on-put.md):
> the v2 API returns the *display* note for recurring-linked txns). Revision 2 replaces the
> `lm_hash` half with an explicit, on-demand snapshot reconcile. The **split-update mechanics,
> crash-resistance ordering, sink protocol, and `ynab_hash` definition below remain valid** and
> are reused as-is; only the LM-side comparison changes.

---

## Revision 2 (2026-06-12): drop `lm_hash`, split sync from reconcile

### Why

`lm_hash` tried to answer "does LM still have what we'd send?" by storing a fingerprint and
recomputing it from the live LM read-back at `--rebuild-index`. Two problems:

1. **It baked the comparison/normalization logic into a stored fingerprint.** Any change to how
   we compare (recurring `notes`, `"[No Payee]"`, split-child inheritance) invalidates every
   stored `lm_hash`, forcing a full `--rebuild-index` re-baseline before the fix takes effect
   — the exact "why do I need to rebuild to apply a one-line fix?" friction we hit.
2. **The read-back is unreliable.** The v2 `GET` returns the recurring item's *display* note
   for recurring-linked txns, so the recomputed `lm_hash` never matches what we'd send →
   permanent phantom "would update" rows that re-PUT forever without converging.

Both vanish if **sync never reads LM back**. The fingerprint we need is purely a function of
YNAB data, which we already have (`ynab_hash`). LM read-back becomes a separate, opt-in
*reconcile* step.

### The two operations (decoupled)

**1. Sync** — the normal `import … import` run. **Offline for reads; only writes hit the API.**

- New txns → insert (unchanged from Phase 1).
- Already-mapped txns → compare `ynab_hash`:
  - `current_ynab_hash == stored` → skip (YNAB unchanged, nothing to push).
  - `current_ynab_hash != stored` → derive the LM payload from current YNAB and `PUT` it
    (PUT is a merge; sending the full derived payload is idempotent), then store the new
    `ynab_hash`.
  - `stored == ""` (freshly built index, no baseline) → **establish baseline**: store
    `current_ynab_hash`, issue no update (assume LM already reflects YNAB; the reconcile step
    is how you verify that assumption).
- No `lm_hash`, no `lm_recurring`, no LM read. Decision table collapses to:

  | `ynab_hash` vs stored | action |
  |---|---|
  | equal | `skip` |
  | differ | `update` (PUT derived payload) |
  | stored empty | `baseline` (store hash, no update) |

  Optional refinement (avoid no-op PUTs when a YNAB change touched only unmapped fields):
  also store a `derived_hash` — a hash of the **YNAB-derived LM payload** (never of an LM
  read-back). Skip the PUT when `derived_hash` is unchanged even though `ynab_hash` changed.
  This is the *only* sanctioned LM-payload hash; because it's computed solely from our own
  derivation it never needs a rebuild and never sees the v2 display-note. (Decide at impl time
  whether the extra field is worth it; plain "PUT on any `ynab_hash` change" is acceptable.)

**2. Reconcile** — an **explicit, separate** operation (e.g. `import … reconcile`, or a
`--refetch` flag). This is the *only* thing that reads LM.

- Fetch the full LM snapshot, save it to `data/<slug>/<lm_id>/transactions.json` (the format
  `export.py` already writes; `--rebuild-index` already saves it), and (re)build the
  `ynab_id → lm_id` id-map index.
- For each mapped txn, compare the **YNAB-derived payload** against the **snapshot** field by
  field through one normalizing comparator `lm_payload_equal(derived, snapshot)` (below).
- **Report** the inconsistencies (LM differs from what YNAB would produce) grouped by field —
  reusing the existing `_log_update_diffs` breakdown. Optionally offer to fix them by pushing
  YNAB→LM (same PUT path as sync) and/or to refresh `ynab_hash` for rows that already match.
- Staleness is acceptable: the snapshot is a cached mirror, refreshed only here. We are not
  trying to detect/preserve external LM edits in the hot path (one-way sync focus). Our own
  sync writes can update the on-disk snapshot in place so a later reconcile doesn't re-flag
  rows we just wrote (optional; or just accept that reconcile re-derives the truth anyway).

### `lm_payload_equal(derived, snapshot)` — the single comparator

All LM-side normalization lives here, in full field values (not a hash), so it can change
freely with **zero re-baselining**. Compares the fields currently in `_LM_HASH_FIELDS`
(`date, amount, payee, category_id, notes, status`, plus `split_children`) with these rules:

- **Recurring `notes`/`payee`:** when `snapshot.recurring_id is not None`, the snapshot's
  `notes`/`payee` are the recurring item's *inherited display* values (v2 quirk) — exclude
  them from the comparison. (Replaces this session's `lm_recurring` flag entirely.)
- **`"[No Payee]"` ≡ `None`:** LM stores the literal `"[No Payee]"` where we send `None`.
- **Split-child inheritance:** a child whose snapshot `payee`/`notes` equals its parent's is
  inheriting it — normalize those to `None` before comparing to our re-derived children
  (which carry `None`). Compare child `amount`/`category_id` strictly.

These three are precisely the open false-positive buckets (`split_children` 345, `payee` 24,
recurring `notes` 490 — see the bug doc); folding them into one comparator clears them without
the rebuild dance.

### Code deltas vs the original design below

- **`sync_state.TxnEntry`:** drop `lm_hash` and `lm_recurring`. Keep `ynab_hash`, `lm_id`,
  `split_done`. (Optionally add `derived_hash`.) Old files load fine — extra keys are ignored,
  missing keys default.
- **`sinks.ScannedTxn` / `InsertResult`:** drop `lm_hash` and `recurring_external`. `scan`
  still returns the id-map (+ `child_map`); the snapshot itself is what reconcile compares.
- **`transactions`:** remove `compute_insert_lm_hash` / `compute_lm_hash` usage from the
  decision path; keep `insert_lm_fields` / `sinks.lm_fields_from_transaction` as the two
  *field extractors* feeding `lm_payload_equal`. `build_transaction_update_plan` for **sync**
  uses only `ynab_hash`; a separate `build_reconcile_report` does the snapshot compare.
- **`import.py`:** split the current `--rebuild-index` into (a) build-id-map (still needed once
  to map ynab↔lm) and (b) the explicit reconcile/refetch. Sync no longer reads LM.
- **Removes this session's work:** the `lm_recurring` plumbing added in commit `6f7c7f5`
  (sync_state field, `InsertResult.recurring_external`, `compute_insert_lm_hash(lm_recurring=)`,
  the `_log_update_diffs` consistency tweak) is superseded — the recurring rule moves into
  `lm_payload_equal`. Net deletion.

### Open decisions (resolve at implementation)

1. **CLI shape:** new `reconcile` subcommand vs `import --refetch`? (Leaning subcommand —
   reads vs writes are genuinely different operations.)
2. **`derived_hash` optimization:** add it to suppress no-op PUTs on unmapped-field changes, or
   keep it simple and PUT on any `ynab_hash` change?
3. **Reconcile auto-fix:** report-only, or offer `--apply` to push YNAB→LM for flagged rows?
4. **Conflict guard:** the original "don't overwrite LM edits" guard depended on `lm_hash`.
   Under one-way-only focus it's dropped from sync; if wanted later it becomes a reconcile-time
   warning ("LM differs but YNAB didn't change since last sync") rather than a sync-time bucket.

The remainder of this document is the **original two-hash design** (largely implemented). Treat
its `lm_hash` / decision-table / `--rebuild-index` portions as superseded by this section; its
split-update mechanics, crash-resistance ordering, sink protocol, and `ynab_hash` are retained.

---

## Background and design decisions

### Why two hashes

YNAB transactions have no per-record `updated_at`. The only change signal is the YNAB
`server_knowledge` delta — but computing a hash of the LM payload from current YNAB data and
comparing it to what was sent at import time is sufficient.

Two hashes cooperate to enforce **one-way sync**:

| `ynab_hash` changed? | `lm_hash` changed? | Action |
|---|---|---|
| No | No | Skip — nothing to do |
| No | Yes | Skip — user edited LM directly, do not overwrite |
| Yes | No | Skip — YNAB changed in a field we don't map (e.g. `import_id`) |
| Yes | Yes | **Update** — YNAB changed and LM has stale data |
| Either empty | — | See §"Empty hashes" below |

**`ynab_hash`**: SHA-256[:16] of the YNAB fields that drive the LM payload. Tells us whether
YNAB changed since the last import. Empty after `--rebuild-index` (we can't know what YNAB
looked like at import time).

**`lm_hash`**: SHA-256[:16] of the LM payload fields that were actually written. Tells us
whether LM currently has what we'd send today. Populated at import time and at
`--rebuild-index` time (from actual LM data). Only meaningful when `ynab_hash` is also set;
alone it cannot distinguish "YNAB changed" from "user edited LM."

### Empty hashes (post-rebuild exception)

After `--rebuild-index`, `ynab_hash` is `""` but `lm_hash` is populated from actual LM data.
With no `ynab_hash` baseline, the one-way sync guarantee cannot be enforced: any divergence
between current YNAB data and `lm_hash` is treated as a YNAB change and will overwrite LM.
This is unavoidable — document it clearly (see §"--rebuild-index" below) and print a warning.

Entries where both hashes are `""` (never imported, or index freshly built before this feature)
are treated as: compute and store both hashes from current YNAB data, issue no update. This
initialises the baseline without touching LM.

### server_knowledge fast-path

`SyncStateData` gains `ynab_txn_server_knowledge: int = 0`. After each successful `--apply`
run, set this to `checkpoint["transactions"]` from `data/<slug>/checkpoint.json`. On the next
run, if it matches the current checkpoint value, no transactions have changed at all — skip the
entire update scan (O(1) check instead of O(N) hashes). Performance note: hashing all 12 k
CAD transactions takes ~95 ms either way, so this is a convenience skip, not a critical
optimisation.

### Split update strategy (confirmed by live API experiments)

Confirmed API behaviour:
- `PUT /transactions/{child_lm_id}` — works on split children; can change `category_id`,
  `amount`, `notes`, `payee`; `split_parent_id` is preserved.
- `DELETE /transactions/split/{parent_lm_id}` — unsplits the parent, restores it as a regular
  transaction, returns `{}`. `custom_metadata` is preserved.
- Cannot `DELETE` a child directly; must unsplit the parent first.
- Cannot re-split an already-split parent; must unsplit first.
- `PUT /transactions/{id}` accepts `custom_metadata` (full replacement).

Strategy: prefer in-place per-child PUTs to avoid disruption, fall back to unsplit+resplit
only when the sub structure changes.

**Non-structural change** (same set of YNAB sub IDs, just field edits): issue per-child
`PUT /transactions/{child_lm_id}` for each changed sub, plus `PUT /transactions/{parent_lm_id}`
if parent-level fields (date, payee, notes, status) changed. No unsplit needed.

**Structural change** (sub added or removed, detected by comparing YNAB sub ID sets):
1. `DELETE /transactions/split/{parent_lm_id}` (unsplit)
2. Set `sync.txn(ynab_id).split_done = False` and clear the old child entries from
   `split_children`
3. `PUT /transactions/{parent_lm_id}` with updated parent fields if needed
4. Re-run Pass 2: `sink.split(parent_lm_id, new_children)`
5. Update `split_children` with new child IDs; set `split_done = True`

---

## Experimental findings (resolved open questions)

**Q1 unsplit endpoint**: `DELETE /transactions/split/{parent_lm_id}` → `{}`. Confirmed.
Add `LMClient.unsplit_transaction(transaction_id: int) -> None`.

**Q2 split child PUT**: `PUT /transactions/{child_id}` works for any mutable field
(`category_id`, `amount`, `notes`, `payee`). Confirmed.

**Q3 custom_metadata updatable**: `PUT /transactions/{id}` with `{"custom_metadata": {...}}`
performs a full replacement and returns the updated object. Confirmed.

**Q4 reconciled transactions**: Reconciled YNAB transactions (`cleared: "reconciled"`) should
still be updated in LM if YNAB fields changed. No special handling.

---

## File changes

### 1. `lunchmoney/sync_state.py`

#### 1a. Extend `TxnEntry`

```python
class TxnEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    lm_id: int
    split_done: bool = False
    ynab_hash: str = ""   # hash of YNAB input fields at last import
    lm_hash: str = ""     # hash of LM payload fields at last import
    synced_at: str = ""
```

#### 1b. Add `split_children` to `SyncStateData`

This was already planned in the Phase 1 implementation doc (§4.2) but not yet coded. It is
required by the update path to locate child LM IDs for per-child PUTs.

```python
class SyncStateData(BaseModel):
    ...
    # YNAB sub.id -> LM child transaction id. Keyed by sub ID (not parent).
    # Populated during Pass 2 split. Used for per-child updates.
    split_children: dict[str, int] = Field(default_factory=dict)
    ...
    ynab_txn_server_knowledge: int = 0   # NEW: checkpoint value at last successful --apply
```

#### 1c. Add `SyncState` accessors for `split_children` and `ynab_txn_server_knowledge`

```python
def split_child_lm_id(self, ynab_sub_id: str) -> Optional[int]:
    return self._d.split_children.get(ynab_sub_id)

def set_split_child(self, ynab_sub_id: str, lm_child_id: int) -> None:
    self._d.split_children[ynab_sub_id] = lm_child_id

def clear_split_children_for(self, ynab_sub_ids: list[str]) -> None:
    """Remove stale child entries when a split is being redone."""
    for sid in ynab_sub_ids:
        self._d.split_children.pop(sid, None)

@property
def ynab_txn_server_knowledge(self) -> int:
    return self._d.ynab_txn_server_knowledge

def set_ynab_txn_server_knowledge(self, value: int) -> None:
    self._d.ynab_txn_server_knowledge = value
```

#### 1d. Update `set_txn()` signature

```python
def set_txn(self, ynab_id: str, *, lm_id: int, split_done: bool = False,
            ynab_hash: str = "", lm_hash: str = "") -> None:
    self._d.transactions[ynab_id] = TxnEntry(
        lm_id=lm_id, split_done=split_done,
        ynab_hash=ynab_hash, lm_hash=lm_hash, synced_at=_now(),
    )
```

Existing callers that omit `ynab_hash`/`lm_hash` default to `""` — backwards compatible.

#### 1e. Add hash functions (module-level, no I/O)

```python
import hashlib
import json as _json

def compute_ynab_hash(txn: dict) -> str:
    """Hash the YNAB fields that drive the LM payload."""
    key: dict = {
        "date":        txn["date"],
        "amount":      txn["amount"],          # milliunits int
        "category_id": txn.get("category_id"),
        "payee_name":  txn.get("payee_name"),
        "memo":        txn.get("memo"),
        "approved":    txn.get("approved"),
        "flag_color":  txn.get("flag_color"),
    }
    subs = [s for s in (txn.get("subtransactions") or []) if not s.get("deleted")]
    if subs:
        key["subtransactions"] = sorted(
            [{"amount": s["amount"], "category_id": s.get("category_id"),
              "payee_name": s.get("payee_name"), "memo": s.get("memo")}
             for s in subs],
            key=lambda s: _json.dumps(s, sort_keys=True),
        )
    return hashlib.sha256(_json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def compute_lm_hash(fields: dict) -> str:
    """Hash the LM payload fields that are written / compared.

    *fields* must be a plain dict with string keys. Pass either:
    - insert.model_dump(mode="json", exclude_none=False) at import time, or
    - a dict extracted from TransactionObject at rebuild time.
    Date must be an ISO string ("YYYY-MM-DD") in both cases.
    For split parents, include "split_children" key (see below).
    """
    key = {
        "date":        fields.get("date"),        # ISO string
        "amount":      fields.get("amount"),       # decimal string "X.XXXX"
        "payee":       fields.get("payee"),
        "category_id": fields.get("category_id"),
        "notes":       fields.get("notes"),
        "status":      fields.get("status"),
    }
    # For split parents: include sorted child fields so any sub change shifts the hash
    children = fields.get("split_children")
    if children:
        key["split_children"] = sorted(
            [{"amount": c.get("amount"), "category_id": c.get("category_id"),
              "notes": c.get("notes"), "payee": c.get("payee")}
             for c in children],
            key=lambda c: _json.dumps(c, sort_keys=True),
        )
    return hashlib.sha256(_json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
```

`custom_metadata` and `external_id` are intentionally excluded from `lm_hash` — updates to
those fields don't constitute a semantic change worth overwriting user edits.

#### 1f. Update `mark_split_done` to accept child map

```python
def mark_split_done(self, ynab_id: str,
                    child_map: Optional[dict[str, int]] = None) -> None:
    e = self._d.transactions.get(ynab_id)
    if e:
        e.split_done = True
        e.synced_at = _now()
    if child_map:
        self._d.split_children.update(child_map)
```

---

### 2. `lunchmoney/lm_client.py`

Add one method:

```python
def unsplit_transaction(self, transaction_id: int) -> None:
    """DELETE /transactions/split/{id} — restores a split parent to a regular transaction."""
    self._request("DELETE", f"/transactions/split/{transaction_id}")
```

---

### 3. `lunchmoney/sinks.py`

#### 3a. Extend `ScannedTxn` with `lm_hash`

```python
@dataclass
class ScannedTxn:
    ynab_id: str
    lm_id: int
    split_done: bool = False
    lm_hash: str = ""           # NEW: hash of LM fields at scan time
    child_map: dict[str, int] = field(default_factory=dict)  # NEW: sub_ynab_id -> child_lm_id
```

`child_map` is populated only for split parents (`is_split_parent=True`). It maps YNAB sub IDs
from `custom_metadata.ynab_id` on each child to `child.id`. Since LM split children have no
`custom_metadata`, we can't recover sub IDs from LM at scan time; see §"Rebuild limitation for
split children" below.

#### 3b. Update `ApiSink.scan_imported()`

Extend to also pass `include_children=true` so split parents come back with their children
array populated, and to compute `lm_hash`:

```python
def scan_imported(self) -> list[ScannedTxn]:
    from sync_state import compute_lm_hash  # avoid circular import
    txns = self._client.get_transactions(
        start_date="1900-01-01", end_date="2100-01-01",
        include_split_parents="true",
        include_children="true",
    )
    result: list[ScannedTxn] = []
    for t in txns:
        ynab_id = (t.custom_metadata or {}).get("ynab_id")
        if not ynab_id:
            continue
        date_str = t.date.isoformat() if hasattr(t.date, "isoformat") else str(t.date)
        fields: dict[str, Any] = {
            "date": date_str, "amount": t.amount, "payee": t.payee,
            "category_id": t.category_id, "notes": t.notes,
            "status": t.status.value if hasattr(t.status, "value") else t.status,
        }
        if t.is_split_parent and t.children:
            fields["split_children"] = [
                {"amount": c.amount, "category_id": c.category_id,
                 "notes": c.notes, "payee": c.payee}
                for c in t.children
            ]
        lm_hash = compute_lm_hash(fields)
        result.append(ScannedTxn(
            ynab_id=str(ynab_id), lm_id=t.id,
            split_done=bool(t.is_split_parent),
            lm_hash=lm_hash,
        ))
    return result
```

**Rebuild limitation for split children**: LM children have no `custom_metadata.ynab_id`, so
we cannot recover the sub-id → child-lm-id mapping from a scan. After `--rebuild-index`, the
`split_children` map in sync_state is empty for rebuilt entries. This means the first
re-import after a rebuild cannot do per-child PUTs for split parents; it will fall back to
unsplit+resplit for any split whose hash changed. This is acceptable: `--rebuild-index` is a
recovery tool, not a routine operation.

#### 3c. Add `update` to `TransactionSink` protocol and implementations

```python
class TransactionSink(Protocol):
    def scan_imported(self) -> list[ScannedTxn]: ...
    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult: ...
    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> None: ...
    def unsplit(self, parent_lm_id: int) -> None: ...   # NEW
    def update(self, lm_id: int, payload: dict[str, Any]) -> None: ...  # NEW
    def close(self) -> None: ...
```

`ApiSink.unsplit`: calls `client.unsplit_transaction(lm_id)`.
`ApiSink.update`: calls `client.update_transaction(lm_id, payload)`.

`DirSink.unsplit`: appends `{"parent_lm_id": lm_id}` to `self._unsplits` list.
`DirSink.update`: appends `{"lm_id": lm_id, "payload": payload}` to `self._updates` list.
`DirSink.close`: writes `unsplit_pass.json` (sorted by `parent_lm_id`) and `updates.json`
(sorted by `lm_id`) in addition to existing files.

---

### 4. `lunchmoney/transactions.py`

Add two pure functions for the update path.

#### 4a. `compute_insert_lm_hash(insert: InsertTransactionObject, split_children: list[SplitTransactionObject] | None = None) -> str`

```python
from sync_state import compute_lm_hash

def compute_insert_lm_hash(
    insert: InsertTransactionObject,
    split_children: Optional[list[SplitTransactionObject]] = None,
) -> str:
    """Compute lm_hash from the InsertTransactionObject that was (or will be) sent."""
    d = insert.model_dump(mode="json", exclude_none=False)
    # date is serialized as ISO string by model_dump(mode="json")
    fields = {
        "date": d.get("date"), "amount": d.get("amount"), "payee": d.get("payee"),
        "category_id": d.get("category_id"), "notes": d.get("notes"),
        "status": d.get("status"),
    }
    if split_children:
        fields["split_children"] = [
            {"amount": c.amount, "category_id": c.category_id,
             "notes": c.notes, "payee": c.payee}
            for c in split_children
        ]
    return compute_lm_hash(fields)
```

#### 4b. `TxnUpdateBuckets` and update plan types

```python
UPDATE_BUCKETS = (
    "update_regular",          # non-split txn: YNAB changed, issue PUT
    "update_split_inplace",    # split: sub fields changed, issue per-child PUTs
    "update_split_structural", # split: subs added/removed, unsplit+resplit
    "skipped_no_change",       # hashes match — nothing to do
    "skipped_lm_edited",       # ynab_hash unchanged, lm_hash changed — user edited LM
    "skipped_ynab_unmapped",   # YNAB changed in field we don't map (ynab changed, lm same)
    "skipped_split_no_children", # split parent, split_children map missing → can't do inplace
    "conflict",                # both ynab_hash and lm_hash changed → skip, warn
)

@dataclass
class TxnUpdateItem:
    ynab_id: str
    lm_id: int
    bucket: str
    payload: Optional[dict[str, Any]] = None        # for update_regular
    parent_payload: Optional[dict[str, Any]] = None # for update_split_*: parent PUT body
    child_updates: Optional[list[tuple[int, dict[str, Any]]]] = None  # (child_lm_id, payload)
    new_children: Optional[list[SplitTransactionObject]] = None  # for update_split_structural
    ynab_sub_ids: Optional[list[str]] = None        # new YNAB sub IDs, same order as new_children
    old_sub_ids: Optional[list[str]] = None         # previous YNAB sub IDs to clear from split_children
    new_ynab_hash: str = ""
    new_lm_hash: str = ""
    note: str = ""

@dataclass
class TransactionUpdatePlan:
    items: list[TxnUpdateItem]
    counts: dict[str, int]
    conflicts: list[TxnUpdateItem]
```

#### 4c. `build_transaction_update_plan()`

```python
def build_transaction_update_plan(
    ynab_txns: list[dict[str, Any]],
    ynab_accounts: list[dict[str, Any]],
    *,
    sync: SyncState,
    options: TxnImportOptions,
) -> TransactionUpdatePlan:
    """Pure. For each YNAB txn already in sync.transactions, decide whether an LM update
    is needed and what kind. No I/O."""
    from sync_state import compute_ynab_hash, compute_lm_hash
    accts_by_id = {a["id"]: a for a in ynab_accounts}
    items: list[TxnUpdateItem] = []

    for txn in ynab_txns:
        ynab_id = txn["id"]
        entry = sync.txn(ynab_id)
        if entry is None:
            continue  # not imported yet — insert plan handles new txns
        if txn.get("deleted"):
            continue  # deleted YNAB txns are never updated

        current_ynab_hash = compute_ynab_hash(txn)
        # Re-classify to get the LM payload (same logic as insert)
        classified = _classify_txn(txn, accts_by_id, sync, options)
        if classified.insert is None:
            continue  # skipped bucket — nothing to update

        new_lm_hash = compute_insert_lm_hash(classified.insert, classified.split_children)
        stored_ynab = entry.ynab_hash
        stored_lm   = entry.lm_hash

        # Determine which hashes changed (empty = unknown)
        ynab_changed = stored_ynab != "" and stored_ynab != current_ynab_hash
        lm_changed   = stored_lm   != "" and stored_lm   != new_lm_hash
        both_unknown = stored_ynab == "" and stored_lm == ""

        if both_unknown:
            # First run after feature was added (or post-rebuild with no lm_hash either)
            # Store hashes, issue no update
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_no_change",
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                note="initialising hash baseline",
            ))
            continue

        if stored_ynab == "" and stored_lm != "":
            # Post-rebuild: lm_hash known, ynab_hash unknown
            if not lm_changed:
                items.append(TxnUpdateItem(
                    ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_no_change",
                    new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                ))
                continue
            # lm_hash changed — treat as YNAB change (one-way sync guarantee suspended
            # post-rebuild), fall through to update logic below
            ynab_changed = True

        if not ynab_changed and not lm_changed:
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_no_change",
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
            ))
            continue

        if not ynab_changed and lm_changed:
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_lm_edited",
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                note="LM was edited directly — not overwriting",
            ))
            continue

        if ynab_changed and not lm_changed:
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_ynab_unmapped",
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                note="YNAB changed in a field we don't map to LM",
            ))
            continue

        # Both changed — conflict if both hashes were previously known
        if stored_ynab != "" and stored_lm != "":
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="conflict",
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                note="both YNAB and LM changed since last import",
            ))
            continue

        # ynab_changed=True, lm_changed=True (or post-rebuild lm_changed=True)
        is_split = classified.split_children is not None

        if not is_split:
            # Regular transaction: compute PUT payload (only mutable fields, not external_id)
            insert_dict = classified.insert.model_dump(mode="json", exclude_none=True)
            insert_dict.pop("external_id", None)
            insert_dict.pop("manual_account_id", None)
            insert_dict.pop("plaid_account_id", None)
            insert_dict.pop("currency", None)
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_regular",
                payload=insert_dict,
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
            ))
            continue

        # Split parent
        subs = [s for s in (txn.get("subtransactions") or []) if not s.get("deleted")]
        current_sub_ids = {s["id"] for s in subs}
        known_sub_ids   = {sid for sid in current_sub_ids
                           if sync.split_child_lm_id(sid) is not None}

        if not known_sub_ids:
            # No split_children map (e.g. post-rebuild) — must unsplit+resplit
            parent_payload = _parent_only_payload(classified.insert)
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_split_structural",
                parent_payload=parent_payload,
                new_children=classified.split_children,
                ynab_sub_ids=[s["id"] for s in subs],
                old_sub_ids=[],   # nothing to clear
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                note="no child map (post-rebuild) — unsplit+resplit",
            ))
            continue

        # Check for structural change: subs added or removed
        if current_sub_ids != known_sub_ids:
            parent_payload = _parent_only_payload(classified.insert)
            # old_sub_ids = all sub IDs currently in split_children for this parent
            # (i.e. the sub IDs from the previous split, which we need to clear)
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_split_structural",
                parent_payload=parent_payload,
                new_children=classified.split_children,
                ynab_sub_ids=[s["id"] for s in subs],
                old_sub_ids=list(known_sub_ids),
                new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
                note="sub structure changed",
            ))
            continue

        # Non-structural: build per-child update list
        child_updates: list[tuple[int, dict[str, Any]]] = []
        for sub, lm_child in zip(subs, classified.split_children or []):
            child_lm_id = sync.split_child_lm_id(sub["id"])
            if child_lm_id is None:
                continue
            child_payload = {
                "amount": lm_child.amount,
                "category_id": lm_child.category_id,
                "notes": lm_child.notes,
                "payee": lm_child.payee,
                "date": lm_child.date.isoformat() if lm_child.date else None,
            }
            child_payload = {k: v for k, v in child_payload.items() if v is not None}
            child_updates.append((child_lm_id, child_payload))

        parent_payload = _parent_only_payload(classified.insert)
        items.append(TxnUpdateItem(
            ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_split_inplace",
            parent_payload=parent_payload,
            child_updates=child_updates,
            new_ynab_hash=current_ynab_hash, new_lm_hash=new_lm_hash,
        ))

    counts = {b: sum(1 for i in items if i.bucket == b) for b in UPDATE_BUCKETS}
    conflicts = [i for i in items if i.bucket == "conflict"]
    return TransactionUpdatePlan(items=items, counts=counts, conflicts=conflicts)


def _parent_only_payload(insert: InsertTransactionObject) -> dict[str, Any]:
    """PUT payload for the split parent (parent-level fields only, no account/currency)."""
    d = insert.model_dump(mode="json", exclude_none=True)
    for k in ("external_id", "manual_account_id", "plaid_account_id", "currency"):
        d.pop(k, None)
    return d
```

Note: `_classify_txn` is called with the same `options` as the insert path. Transactions
filtered by `--since` have `insert=None` and are skipped above. Opening balance transactions
and transfers are re-classified normally — their LM representation can change if their YNAB
category or memo changed.

---

### 5. `lunchmoney/import.py`

#### 5a. Update `_reconcile_txn_index` to store `lm_hash`

```python
def _reconcile_txn_index(sink: TransactionSink, sync: SyncState, sync_dir: Path,
                         *, force: bool) -> None:
    if not force and sync.txn_index_built:
        return
    if force:
        print("  Rebuilding local transaction index from Lunch Money (--rebuild-index)...")
        sync.clear_transactions()
    else:
        print("  Building local transaction index from Lunch Money (first run)...")
    scanned = sink.scan_imported()
    for s in scanned:
        # ynab_hash intentionally left "" — we can't recover what YNAB looked like at import.
        # lm_hash is populated from actual LM data so update detection works post-rebuild.
        sync.set_txn(s.ynab_id, lm_id=s.lm_id, split_done=s.split_done, lm_hash=s.lm_hash)
        # Restore split_child map if scan provided it
        for sub_ynab_id, child_lm_id in s.child_map.items():
            sync.set_split_child(sub_ynab_id, child_lm_id)
    sync.set_txn_index_built(True)
    sync.save(sync_dir)
    empties = sum(1 for s in scanned if not s.lm_hash)
    print(f"  Indexed {len(scanned)} already-imported transaction(s).")
    if force and any(s.ynab_id for s in scanned):
        print(f"  {YELLOW}⚠{RESET}  One-way sync guarantee is suspended for this index: "
              f"ynab_hash is empty after --rebuild-index. Review dry-run output carefully "
              f"before --apply.")
```

#### 5b. Update Pass 1 in `phase_transactions` to store hashes

After inserting new transactions and recording `lm_id` in sync_state, also store both hashes.
The insert loop already has the `ClassifiedTxn` items and `InsertTransactionObject`s. Change:

```python
# OLD:
for ynab_id, lm_id in result.id_by_external.items():
    sync.set_txn(ynab_id, lm_id=lm_id)

# NEW:
ynab_txn_by_id = {t["id"]: t for t in ynab_txns}
for ynab_id, lm_id in result.id_by_external.items():
    ynab_txn = ynab_txn_by_id.get(ynab_id)
    # Find the ClassifiedTxn to get its insert + split_children for lm_hash
    classified_by_id = {item.ynab_id: item for item in new_items}
    c = classified_by_id.get(ynab_id)
    yh = compute_ynab_hash(ynab_txn) if ynab_txn else ""
    lh = compute_insert_lm_hash(c.insert, c.split_children) if (c and c.insert) else ""
    sync.set_txn(ynab_id, lm_id=lm_id, ynab_hash=yh, lm_hash=lh)
```

Import `compute_ynab_hash` from `sync_state` and `compute_insert_lm_hash` from `transactions`
at the top of `import.py`. Also ensure `datetime` and `timezone` are imported (they already are
via the existing `from datetime import date, datetime, timezone` import).

Also update `mark_split_done` calls in Pass 2 to record the child map:

```python
# In the Pass 2 loop, after sink.split():
# The split response contains children ordered the same as we sent them.
# Match by position to get sub_id -> child_lm_id.
# But split() currently returns None. Change lm_client.split_transaction to return
# the children list, and thread it back through the sink.
```

This requires a small change: `ApiSink.split()` must return the child IDs. See §5c below.

#### 5c. Thread split child IDs back through the sink

Change `TransactionSink.split` signature:

```python
def split(self, parent_lm_id: int, children: list[SplitTransactionObject]
          ) -> list[int]: ...   # returns list of child LM ids in the same order as children
```

`ApiSink.split`: return `[c.id for c in result.children]` (the split response has `children`).
`DirSink.split`: return synthetic sequential IDs (same as insert; `self._next_id` counter).

In `phase_transactions` Pass 2, after `sink.split(...)`:

```python
child_lm_ids = sink.split(entry.lm_id, req.child_transactions)
# req.child_transactions was built from YNAB subs in the same order.
# The SplitRequest stores ynab_sub_ids alongside child_transactions:
sub_ids = req.ynab_sub_ids  # see §5d
child_map = dict(zip(sub_ids, child_lm_ids))
sync.mark_split_done(req.ynab_parent_id, child_map=child_map)
```

#### 5d. Add `ynab_sub_ids` to `SplitRequest`

In `transactions.py`, `SplitRequest` currently has `ynab_parent_id` and
`child_transactions`. Add:

```python
@dataclass
class SplitRequest:
    ynab_parent_id: str
    child_transactions: list[SplitTransactionObject]
    ynab_sub_ids: list[str]   # NEW: YNAB sub.id for each child, same order as child_transactions
```

In `build_transaction_plan`, where `SplitRequest` is built:

```python
non_deleted_subs = [s for s in subs if not s.get("deleted")]
split_requests.append(SplitRequest(
    ynab_parent_id=classified.ynab_id,
    child_transactions=classified.split_children,
    ynab_sub_ids=[s["id"] for s in non_deleted_subs],
))
```

`_classify_split_children` already iterates non-deleted subs in the same order, so the index
alignment is preserved.

#### 5e. Add `phase_transaction_updates` function

New function called from `phase_transactions` after the existing insert+split passes:

```python
def _apply_update_plan(
    update_plan: TransactionUpdatePlan,
    sink: TransactionSink,
    sync: SyncState,
    sync_dir: Path,
    apply: bool,
) -> int:
    """Print summary and optionally apply transaction updates. Returns count of updates applied."""
    c = update_plan.counts
    verb = "Will" if apply else "Would"

    if c.get("update_regular", 0) or c.get("update_split_inplace", 0) or c.get("update_split_structural", 0):
        n = c.get("update_regular", 0) + c.get("update_split_inplace", 0) + c.get("update_split_structural", 0)
        print(f"  {verb} update  {n:5}  transaction(s) (YNAB changed, LM has stale data)")
    if c.get("skipped_lm_edited", 0):
        print(f"  Skip     {c['skipped_lm_edited']:5}  transaction(s) (LM edited directly — not overwriting)")
    if c.get("conflict", 0):
        print(f"  {YELLOW}Conflict {c['conflict']:5}  transaction(s) — both YNAB and LM changed{RESET}")
    if update_plan.conflicts:
        for item in update_plan.conflicts:
            entry = sync.txn(item.ynab_id)
            print(f"    {item.ynab_id[:8]}…  LM {entry.lm_id if entry else '?'}  — {item.note}")

    if not apply:
        return 0

    applied = 0
    for item in update_plan.items:
        bucket = item.bucket
        if bucket == "update_regular":
            sink.update(item.lm_id, item.payload)
            sync.set_txn(item.ynab_id, lm_id=item.lm_id,
                         split_done=sync.txn(item.ynab_id).split_done,
                         ynab_hash=item.new_ynab_hash, lm_hash=item.new_lm_hash)
            applied += 1

        elif bucket == "update_split_inplace":
            # PUT parent fields, then PUT each changed child
            sink.update(item.lm_id, item.parent_payload)
            for child_lm_id, child_payload in (item.child_updates or []):
                sink.update(child_lm_id, child_payload)
            sync.set_txn(item.ynab_id, lm_id=item.lm_id, split_done=True,
                         ynab_hash=item.new_ynab_hash, lm_hash=item.new_lm_hash)
            applied += 1

        elif bucket == "update_split_structural":
            # Crash-safe ordering — see §"Structural update crash resistance":
            # Step A: mark split_done=False and clear stale child entries BEFORE any API call.
            old_sub_ids = list(item.old_sub_ids or [])
            sync.clear_split_children_for(old_sub_ids)
            entry = sync.txn(item.ynab_id)
            if entry:
                entry.split_done = False
                entry.synced_at = datetime.now(timezone.utc).isoformat()
            sync.save(sync_dir)

            # Step B: unsplit. If LM is already unsplit (crash recovery: step A saved but
            # step B previously failed), the API returns a non-split-parent error — treat
            # as success (the parent is already restored).
            try:
                sink.unsplit(item.lm_id)
            except Exception as e:
                if "not a split parent" not in str(e).lower() and "TRANSACTION_IS_NOT_SPLIT_PARENT" not in str(e):
                    raise

            # Step C: update parent-level fields (date, payee, notes, status)
            if item.parent_payload:
                sink.update(item.lm_id, item.parent_payload)

            # Step D: re-split with new children
            child_lm_ids = sink.split(item.lm_id, item.new_children or [])

            # Step E: record new child map, mark split_done=True, store new hashes
            new_child_map = dict(zip(item.ynab_sub_ids or [], child_lm_ids))
            sync.mark_split_done(item.ynab_id, child_map=new_child_map)
            sync.set_txn(item.ynab_id, lm_id=item.lm_id, split_done=True,
                         ynab_hash=item.new_ynab_hash, lm_hash=item.new_lm_hash)
            sync.save(sync_dir)
            applied += 1

        elif bucket in ("skipped_no_change", "skipped_ynab_unmapped"):
            # Store/refresh hashes even when skipping, so future runs have an accurate baseline
            if item.new_ynab_hash or item.new_lm_hash:
                entry = sync.txn(item.ynab_id)
                if entry:
                    sync.set_txn(item.ynab_id, lm_id=entry.lm_id,
                                 split_done=entry.split_done,
                                 ynab_hash=item.new_ynab_hash, lm_hash=item.new_lm_hash)
        # skipped_lm_edited and conflict: do NOT update hashes (preserve baseline for next run)

    sync.save(sync_dir)
    return applied
```

**Note on `update_split_structural` sub IDs**: To update `split_children` after re-split,
`TxnUpdateItem` needs `ynab_sub_ids`. Add this field to `TxnUpdateItem` and populate it in
`build_transaction_update_plan` from the non-deleted subs list. Then in the apply loop:

```python
child_map = dict(zip(item.ynab_sub_ids or [], child_lm_ids))
sync.mark_split_done(item.ynab_id, child_map=child_map)
```

#### 5f. Wire into `phase_transactions`

At the top of `phase_transactions`, load checkpoint:

```python
checkpoint_path = data_dir / "checkpoint.json"
current_sk = 0
if checkpoint_path.exists():
    import json as _json
    ck = _json.loads(checkpoint_path.read_text())
    current_sk = ck.get("transactions", 0)
```

After `_reconcile_txn_index`, before building the insert plan, check the fast-path:

```python
skip_update_scan = (current_sk != 0 and current_sk == sync.ynab_txn_server_knowledge)
```

The existing early-return guard:

```python
if not new_items and not pending_splits:
    print(f"\n  {GREEN}Nothing to import — all transactions already in local index.{RESET}")
    return 0
```

must be **removed** (or moved to after the update scan). Otherwise a run with no new inserts
skips all updates. Replace it with a combined check at the very end:

```python
if not apply:
    print(f"\n  {DIM}(dry-run — pass --apply to apply){RESET}")
    return len(new_items)
```

(This guard already appears below the plan printing; keep it in place, it is fine — it only
skips the apply loop, not the update scan.)

After the existing insert+split passes:

```python
# Update pass: detect and propagate YNAB changes to already-imported transactions
if not skip_update_scan:
    update_plan = build_transaction_update_plan(
        ynab_txns, ynab_accounts, sync=sync, options=options
    )
    updates_applied = _apply_update_plan(update_plan, sink, sync, sync_dir, apply)
    if apply:
        sync.set_ynab_txn_server_knowledge(current_sk)
        sync.save(sync_dir)
else:
    updates_applied = 0
    print(f"  {DIM}No YNAB changes since last import (server_knowledge matches).{RESET}")

if not new_items and not pending_splits and updates_applied == 0:
    print(f"\n  {GREEN}Nothing to do.{RESET}")
```

Update the return value: `return result.inserted + split_done + updates_applied`.

#### 5g. Add `--force-ynab` flag

Add to `p_import`:

```python
p_import.add_argument(
    "--force-ynab", action="store_true",
    help="When both YNAB and LM have changed (conflict), overwrite LM with YNAB data.",
)
```

Pass as `force_ynab: bool` through `cmd_import` → `phase_transactions` →
`build_transaction_update_plan`. In the update plan builder, change the conflict case:

```python
if stored_ynab != "" and stored_lm != "" and not options.force_ynab:
    items.append(TxnUpdateItem(..., bucket="conflict", ...))
    continue
# else fall through to update logic (force_ynab overrides)
```

Add `force_ynab: bool = False` to `TxnImportOptions`.

Full call chain: `args.force_ynab` → `txn_options = TxnImportOptions(..., force_ynab=args.force_ynab)`
in `main()` → `cmd_import(..., txn_options=txn_options)` (already passed through) →
`phase_transactions(..., options=txn_options)` (already passed through) →
`build_transaction_update_plan(..., options=options)` (already passed through).
No other wiring changes needed.

---

## Structural update crash resistance

The unsplit → re-split sequence has three crash windows. The ordering of sync_state writes is
designed so that every crash leaves the system in a state that the next run can recover from
without data loss or corruption.

### Sync_state invariants that must always hold

1. `split_done=True` implies `split_children` contains an entry for every non-deleted sub of
   that parent's current YNAB split.
2. `split_done=False` with `split_children` entries for a given parent is the signal that
   Pass 2 (or a re-split after structural update) is pending.
3. `split_children` entries that refer to deleted LM child IDs are stale and must not be used
   for PUT calls without first verifying the child still exists.

### Step-by-step ordering and crash analysis

**Step A**: clear `split_children` for old sub IDs; set `split_done=False`; save sync_state.

**Step B**: `sink.unsplit(lm_id)`.

**Step C**: `sink.update(lm_id, parent_payload)` (parent-level field changes if any).

**Step D**: `child_lm_ids = sink.split(lm_id, new_children)`.

**Step E**: record `split_children` with new sub_id→child_lm_id mapping; set `split_done=True`;
store new hashes; save sync_state.

| Crash window | sync_state state | LM state | Recovery on next run |
|---|---|---|---|
| Before step A | `split_done=True`, old `split_children` | split with old children | Recompute update plan → structural update → runs from step A |
| After A, before B | `split_done=False`, no `split_children` | still split (old) | Unsplit succeeds; re-splits with new children |
| After B, before D | `split_done=False`, no `split_children` | unsplit (plain txn) | Unsplit attempted → "not a split parent" error → treated as success; re-splits |
| After D, before E | `split_done=False`, no `split_children` | split with new children | Unsplit attempted → succeeds (LM re-splits were already applied); re-splits again (redundant but correct) |
| After E | `split_done=True`, new `split_children` | split with new children | No action needed |

The "after D, before E" crash causes one extra unsplit+resplit on recovery — the final state is
identical. The new child LM IDs will differ (new rows created), but the data is correct.

### Error handling for step B

`sink.unsplit(lm_id)` must not raise if the parent is already unsplit (crash recovery for
"after B, before D" window). `ApiSink.unsplit` must catch the LM error with code
`TRANSACTION_IS_NOT_SPLIT_PARENT` and treat it as success. `DirSink.unsplit` always succeeds
(no real state to conflict with).

### No stale `split_children` entries

After step E, `split_children` must contain **only** the new sub IDs. The old sub IDs were
cleared in step A. After recovery from a "after D, before E" crash, step A of the retry clears
the empty map (no-op), so the final map after step E has only the new IDs.

---

## Testing

### Fake sink for unit tests

All tests of `_apply_update_plan` use a `FakeSink` that records calls in order and can be
configured to raise at a specific call number to simulate crashes:

```python
@dataclass
class FakeSink:
    calls: list[tuple[str, Any]] = field(default_factory=list)
    fail_at: Optional[int] = None       # raise on the Nth call (0-indexed)
    already_unsplit: bool = False        # simulate "not a split parent" on unsplit

    def update(self, lm_id: int, payload: dict) -> None:
        self._record("update", (lm_id, payload))

    def unsplit(self, lm_id: int) -> None:
        self._record("unsplit", lm_id)
        if self.already_unsplit:
            raise Exception("TRANSACTION_IS_NOT_SPLIT_PARENT")

    def split(self, parent_lm_id: int, children: list) -> list[int]:
        self._record("split", (parent_lm_id, children))
        # Return synthetic child IDs starting from 9000 so they're distinct from insert IDs
        return [9000 + i for i in range(len(children))]

    def _record(self, name: str, arg: Any) -> None:
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError(f"Simulated crash at call {self.fail_at}")
        self.calls.append((name, arg))

    # scan_imported, insert, close are no-ops for update tests
    def scan_imported(self): return []
    def insert(self, txns): return InsertResult(0, 0, {})
    def close(self): pass
```

### Helper: `make_sync` and `make_ynab_txn`

```python
def make_sync(*, transactions: dict = None, split_children: dict = None) -> SyncState:
    """Build a SyncState with pre-populated transactions and split_children."""
    data = SyncStateData(
        ynab_budget_id="test", lm_account_id=1, currency="cad",
        transactions={k: TxnEntry(**v) for k, v in (transactions or {}).items()},
        split_children=split_children or {},
        special_categories={"payment_transfer": 1, "incomplete_split": 2},
        ynab_internal_cats={"inflow": "inflow-id", "uncategorized": "uncat-id"},
    )
    return SyncState(data)

def make_split_ynab_txn(parent_id: str, subs: list[dict],
                         account_id: str = "acct-1") -> dict:
    """Build a minimal YNAB split-parent transaction dict."""
    return {
        "id": parent_id, "date": "2024-01-15",
        "amount": sum(s["amount"] for s in subs),
        "category_id": "split-cat-id", "category_name": "Split",
        "payee_name": "Test Payee", "memo": "test memo",
        "approved": True, "cleared": "cleared", "flag_color": None,
        "account_id": account_id, "deleted": False,
        "transfer_account_id": None, "transfer_transaction_id": None,
        "subtransactions": subs,
    }

def make_sub(sub_id: str, amount: int, category_id: str, memo: str = None) -> dict:
    return {"id": sub_id, "amount": amount, "category_id": category_id,
            "category_name": "Cat", "payee_name": None, "memo": memo, "deleted": False}
```

### Unit tests: sync_state consistency (`tests/test_transaction_updates.py`)

**Hash functions:**

```
test_ynab_hash_tracks_date_change
test_ynab_hash_tracks_amount_change
test_ynab_hash_tracks_category_change
test_ynab_hash_tracks_payee_change
test_ynab_hash_tracks_memo_change
test_ynab_hash_tracks_approved_change
test_ynab_hash_tracks_flag_color_change
test_ynab_hash_ignores_import_id
test_ynab_hash_ignores_cleared
test_ynab_hash_ignores_account_id
test_ynab_hash_subs_order_independent   # same hash regardless of sub order in input
test_ynab_hash_tracks_sub_category_change
test_ynab_hash_tracks_sub_amount_change
test_ynab_hash_tracks_sub_memo_change
test_lm_hash_tracks_date_change
test_lm_hash_tracks_amount_change
test_lm_hash_tracks_category_id_change
test_lm_hash_tracks_payee_change
test_lm_hash_tracks_notes_change
test_lm_hash_tracks_status_change
test_lm_hash_ignores_external_id
test_lm_hash_ignores_custom_metadata
test_lm_hash_split_includes_child_fields
test_lm_hash_split_children_order_independent
test_lm_hash_from_insert_matches_from_transaction_object_fields
    # Build InsertTransactionObject; compute lm_hash via compute_insert_lm_hash.
    # Build equivalent dict as if extracted from TransactionObject (same field values).
    # Assert both hashes are equal.
```

**Decision table (`build_transaction_update_plan`):**

```
test_no_change_skips
test_lm_edited_skips_and_does_not_overwrite
test_ynab_changed_unmapped_field_skips
test_both_changed_is_conflict
test_both_changed_force_ynab_updates
test_regular_txn_update_payload_excludes_external_id
test_regular_txn_update_payload_excludes_account_id
test_regular_txn_update_payload_excludes_currency
test_split_inplace_when_sub_ids_unchanged
test_split_structural_when_sub_added
test_split_structural_when_sub_removed
test_split_structural_when_no_child_map
test_both_hashes_empty_initialises_baseline_no_update
test_ynab_hash_empty_lm_hash_changed_treated_as_ynab_change  # post-rebuild
test_ynab_hash_empty_lm_hash_unchanged_skips
```

**Structural update crash resistance:**

Each test below calls `_apply_update_plan` with a `FakeSink` configured to crash at a specific
step, then calls it again (simulating the next run) and asserts the final state is correct.

```
test_structural_crash_after_step_A
    # Crash: FakeSink.fail_at=0 (crash before unsplit API call)
    # After first call: sync_state has split_done=False, split_children empty.
    # Second call: FakeSink.already_unsplit=False (LM still split).
    # Assert: unsplit called, split called, split_done=True,
    #         split_children == {new_sub_id: 9000} (new IDs from FakeSink).

test_structural_crash_after_step_B
    # Crash: FakeSink.fail_at=1 (crash after unsplit, before split)
    # After first call: split_done=False, split_children empty.
    # Second call: FakeSink.already_unsplit=True (LM already unsplit).
    # Assert: unsplit attempted but "not a split parent" error swallowed,
    #         split called with new children, split_done=True.

test_structural_crash_after_step_D
    # Use a fixture WITH parent_payload so the call order is always:
    #   call 0: unsplit, call 1: update (parent), call 2: split
    # Crash: FakeSink.fail_at=2 (crash during split — step D)
    # Note: step D is the split call. With parent_payload present it is always call index 2.
    # Without parent_payload it would be call index 1; always include parent_payload in this
    # fixture to keep the numbering unambiguous.
    # sync_state after first call: split_done=False, split_children empty.
    # LM state: unsplit + parent updated, split not yet applied.
    # Second call: FakeSink.already_unsplit=False (LM is unsplit, so unsplit call succeeds),
    #              but wait — LM is now an unsplit regular txn, so unsplit would 400.
    # Correction: after crash at split call, LM is UNSPLIT (step D never completed).
    # Second call: FakeSink.already_unsplit=True (simulate LM already unsplit).
    # Assert: unsplit error swallowed, split called, split_done=True,
    #         split_children has new sub IDs.

test_structural_idempotent_full_run
    # Run _apply_update_plan twice with the same input (no crash, no YNAB change between runs).
    # After first run: split_done=True, split_children set, hashes updated.
    # After second run: update plan detects no change (hashes match), zero API calls.
    # Assert: FakeSink.calls is empty on second run.

test_structural_old_sub_ids_cleared_from_split_children
    # Setup: sync_state.split_children has entries for old_sub_ids.
    # After structural update completes (step E):
    # Assert: old_sub_ids are NOT in sync_state.split_children.
    # Assert: new_sub_ids ARE in sync_state.split_children.
    # Assert: no extra entries in split_children beyond the new subs.

test_structural_all_new_subs_recorded
    # Structural update adds 3 new subs.
    # Assert: sync_state.split_children has exactly 3 entries after step E.
    # Assert: each entry maps to a distinct child_lm_id.

test_structural_child_lm_ids_differ_from_old
    # FakeSink returns 9000, 9001, 9002 as new child IDs.
    # Old split_children had entries pointing to 100, 101.
    # After update: split_children has 9000/9001/9002, NOT 100/101.
```

**Inplace update consistency:**

```
test_inplace_parent_and_all_children_updated
    # YNAB: parent memo changed, sub-A category changed.
    # Assert: FakeSink.calls contains update(parent_lm_id, ...) and update(child_A_lm_id, ...).
    # Assert: sync_state ynab_hash and lm_hash updated after apply.

test_inplace_partial_crash_recovery
    # FakeSink.fail_at=1 (crash after parent PUT, before first child PUT)
    # sync_state not yet updated (crash before step 3 in inplace path).
    # Second run: hashes still show mismatch → retry all PUTs.
    # Assert: all child updates applied on second run (idempotent).

test_inplace_does_not_touch_split_children_map
    # After inplace update, split_children map is unchanged (same sub_id → child_lm_id).
    # Assert: split_children before == split_children after.
```

**sync_state invariant checker (helper used in all tests):**

```python
def assert_sync_state_consistent(
    sync: SyncState,
    ynab_txn: dict,
    all_known_sub_ids: set[str],   # all sub IDs that have ever been assigned to this parent
) -> None:
    """Assert split-parent invariants hold.

    all_known_sub_ids: the union of old_sub_ids and new ynab_sub_ids for this parent,
    used to assert no stale entries survive in split_children.
    Pass it from the test which knows what sub IDs it set up.
    """
    ynab_id = ynab_txn["id"]
    entry = sync.txn(ynab_id)
    assert entry is not None
    current_subs = [s for s in ynab_txn.get("subtransactions", []) if not s.get("deleted")]
    current_sub_ids = {s["id"] for s in current_subs}

    if entry.split_done:
        # Every current sub must have a child_lm_id
        for sub in current_subs:
            assert sync.split_child_lm_id(sub["id"]) is not None, \
                f"split_done=True but no child_lm_id for sub {sub['id']}"
        # No stale old sub IDs from before the structural update
        stale = all_known_sub_ids - current_sub_ids
        for sid in stale:
            assert sync.split_child_lm_id(sid) is None, \
                f"Stale split_children entry for old sub {sid} still present"
    else:
        # split_done=False: split_children entries for this parent's subs must be absent
        # (they were cleared in step A)
        for sid in all_known_sub_ids:
            assert sync.split_child_lm_id(sid) is None, \
                f"split_done=False but split_children[{sid}] is set (should have been cleared)"
```

Call `assert_sync_state_consistent` after every step in the crash-resistance tests,
passing the union of old and new sub IDs for the parent under test.

### Golden / end-to-end tests

Extend `tests/fixtures/` with `update_flow/`:
- `export/transactions.json` — YNAB transactions, some with changed fields vs baseline
- `export/accounts.json`, `export/categories.json`, `export/export_metadata.json`
- `export/checkpoint.json` — `{"transactions": 99}` (non-zero, differs from sync_state value)
- `sync_state.json` — pre-populated with hashes matching the *old* YNAB data, and
  `ynab_txn_server_knowledge=0` so the fast-path is not triggered
- `expected_updates.json` — golden output for `updates.json`
- `expected_unsplit_pass.json` — golden output for `unsplit_pass.json`

Run `import transactions --to-dir <tmp> --apply` and diff against goldens. Regenerate with
`UPDATE_GOLDENS=1`.

Include at least:
- One regular transaction update (category changed)
- One split inplace update (sub category changed, no structural change)
- One structural split update (sub added)
- One skipped-lm-edited transaction (ynab_hash same, lm_hash differs in fixture)
- One conflict (both hashes differ)

---

## Implementation order

1. **`sync_state.py`**: `TxnEntry` + `SyncStateData` fields, accessors, hash functions,
   updated `set_txn` and `mark_split_done`. Unit test hash functions.

2. **`transactions.py`**: add `ynab_sub_ids` to `SplitRequest`; add `compute_insert_lm_hash`;
   add `TxnUpdateItem`, `TransactionUpdatePlan`, `UPDATE_BUCKETS`; add
   `build_transaction_update_plan`. Unit test all decision-table rows.

3. **`lm_client.py`**: add `unsplit_transaction`.

4. **`sinks.py`**: extend `ScannedTxn`; update `ApiSink.scan_imported` (add `include_children`,
   compute `lm_hash`); update `ApiSink.split` to return child IDs; add `ApiSink.update`,
   `ApiSink.unsplit`; add `DirSink.update`, `DirSink.unsplit`, updated `close`.

5. **`import.py`**: update `_reconcile_txn_index` (store `lm_hash` and child map from scan);
   update Pass 1 to store hashes on `set_txn`; update Pass 2 to capture child IDs and call
   `mark_split_done` with `child_map`; add `_apply_update_plan`; wire update scan into
   `phase_transactions`; add `--force-ynab` CLI flag.

6. **Tests**: hash unit tests, update plan unit tests, DirSink golden tests.
