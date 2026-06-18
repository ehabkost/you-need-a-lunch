"""Pure transaction classification + YNAB→LM conversion core.

No I/O, no network. Feed this YNAB transaction dicts + SyncState, get back
a TransactionPlan describing what to insert and in what bucket.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from lm_api_types_generated import InsertTransactionObject, SplitTransactionObject
from sync_state import AccountEntry, SyncState

# Balance adjustment payee substring (YNAB uses several variants)
_BALANCE_ADJ_KEYWORD = "Balance Adjustment"

# YNAB category names that need special handling
_CAT_INFLOW = "Inflow: Ready to Assign"
_CAT_UNCATEGORIZED = "Uncategorized"
_CAT_SPLIT = "Split"
_CAT_DEFERRED = "Deferred Income SubCategory"

BUCKETS = (
    "spending",
    "income",
    "uncategorized",
    "transfer_paired",
    "transfer_one_sided",
    "opening_balance",
    "tracking",
    "balance_adjustment",
    "splits_native",
    "skipped_zero",
    "skipped_deleted",
    "skipped_before_since",
    "needs_decision",
)


@dataclass(frozen=True)
class TxnImportOptions:
    since: Optional[date] = None
    opening_balance_category: Optional[int] = None
    deferred_income_as: Optional[str] = None   # "income" | "uncategorized" | "skip"


@dataclass
class ClassifiedTxn:
    ynab_id: str
    bucket: str
    insert: Optional[InsertTransactionObject] = None
    note: str = ""
    split_children: Optional[list[SplitTransactionObject]] = None  # only for splits_native


@dataclass
class SplitRequest:
    ynab_parent_id: str
    child_transactions: list[SplitTransactionObject]
    ynab_sub_ids: list[str] = field(default_factory=list)  # YNAB sub.id, same order as child_transactions


@dataclass
class TransactionPlan:
    items: list[ClassifiedTxn]
    counts: dict[str, int]
    needs_decision: list[ClassifiedTxn]
    split_requests: list[SplitRequest]


def _lm_amount(milliunits: int) -> str:
    """Convert YNAB milliunits to LM amount string. Negates sign (YNAB and LM conventions differ)."""
    return f"{-milliunits / 1000:.4f}"


def _base_meta(txn: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {"ynab_id": txn["id"]}
    if txn.get("flag_color"):
        meta["ynab_flag_color"] = txn["flag_color"]
    return meta


def _make_insert(
    txn: dict[str, Any],
    *,
    acct_entry: AccountEntry,
    currency: str,
    category_id: Optional[int],
    custom_metadata: dict[str, Any],
    payee_override: Optional[str] = None,
    amount_override: Optional[str] = None,
) -> InsertTransactionObject:
    is_manual = acct_entry.lm_type == "manual"
    return InsertTransactionObject(
        date=date.fromisoformat(txn["date"]),
        amount=amount_override if amount_override is not None else _lm_amount(txn["amount"]),
        currency=currency or None,
        payee=payee_override if payee_override is not None else txn.get("payee_name"),
        category_id=category_id,
        notes=txn.get("memo") or None,
        status="reviewed" if txn.get("approved") else "unreviewed",
        manual_account_id=acct_entry.lm_id if is_manual else None,
        plaid_account_id=acct_entry.lm_id if not is_manual else None,
        external_id=txn["id"],
        custom_metadata=custom_metadata,
    )


def _classify_split_children(
    subs: list[dict[str, Any]],
    parent_txn: dict[str, Any],
    src_account_off_budget: bool,
    sync: SyncState,
) -> list[SplitTransactionObject]:
    """Build the pass-2 split children from YNAB subtransactions."""
    children: list[SplitTransactionObject] = []
    for sub in subs:
        if sub.get("deleted"):
            continue

        sub_cat_id = sub.get("category_id")
        sub_cat_name = sub.get("category_name")

        if sub.get("transfer_account_id"):
            category_id = sync.special_cat_id("payment_transfer")
        elif sub_cat_name == _CAT_UNCATEGORIZED or (
            sub_cat_id and sub_cat_id == sync.ynab_internal_cat("uncategorized")
        ):
            category_id = None
        elif src_account_off_budget:
            # Off-budget account: override non-transfer children to tracking category
            category_id = sync.special_cat_id("tracking_off_budget")
        else:
            category_id = sync.lm_category_id(sub_cat_id) if sub_cat_id else None

        children.append(SplitTransactionObject(
            amount=_lm_amount(sub["amount"]),
            payee=sub.get("payee_name"),
            date=date.fromisoformat(parent_txn["date"]),
            category_id=category_id,
            notes=sub.get("memo") or None,
        ))
    return children


def _classify_txn(
    txn: dict[str, Any],
    accts_by_id: dict[str, dict[str, Any]],
    sync: SyncState,
    options: TxnImportOptions,
) -> ClassifiedTxn:
    ynab_id = txn["id"]

    if txn.get("deleted"):
        return ClassifiedTxn(ynab_id=ynab_id, bucket="skipped_deleted")

    txn_date = date.fromisoformat(txn["date"])
    amount_milliunits: int = txn.get("amount", 0)
    account_id: str = txn["account_id"]
    cat_name: Optional[str] = txn.get("category_name")
    cat_id: Optional[str] = txn.get("category_id")
    payee_name: Optional[str] = txn.get("payee_name")
    subs: list[dict[str, Any]] = txn.get("subtransactions") or []

    acct_entry = sync.account(account_id)
    if acct_entry is None or acct_entry.lm_type == "skipped":
        return ClassifiedTxn(
            ynab_id=ynab_id, bucket="skipped_deleted",
            note=f"account {account_id} not migrated (pre-flight should have caught this)",
        )

    ynab_acct = accts_by_id.get(account_id, {})
    src_account_off_budget = not ynab_acct.get("on_budget", True)
    currency = sync.currency

    is_transfer = txn.get("transfer_account_id") is not None
    is_starting_balance = payee_name == "Starting Balance"
    is_zero = amount_milliunits == 0
    is_split = cat_name == _CAT_SPLIT and bool(subs)

    # ── Split parents (two-pass) ──────────────────────────────────────────────
    if is_split:
        incomplete_split_id = sync.special_cat_id("incomplete_split")
        meta = {**_base_meta(txn), "ynab_is_split_parent": True}
        insert = _make_insert(
            txn,
            acct_entry=acct_entry,
            currency=currency,
            category_id=incomplete_split_id,
            custom_metadata=meta,
        )
        children = _classify_split_children(subs, txn, src_account_off_budget, sync)
        if options.since and txn_date < options.since:
            return ClassifiedTxn(ynab_id=ynab_id, bucket="skipped_before_since")
        return ClassifiedTxn(
            ynab_id=ynab_id, bucket="splits_native",
            insert=insert, split_children=children,
        )

    # ── Starting Balance ──────────────────────────────────────────────────────
    if is_starting_balance:
        if is_zero:
            return ClassifiedTxn(ynab_id=ynab_id, bucket="skipped_zero")
        meta = {**_base_meta(txn), "ynab_starting_balance": True}
        insert = _make_insert(
            txn,
            acct_entry=acct_entry,
            currency=currency,
            category_id=options.opening_balance_category,
            custom_metadata=meta,
        )
        return ClassifiedTxn(ynab_id=ynab_id, bucket="opening_balance", insert=insert)

    # ── Transfers ─────────────────────────────────────────────────────────────
    if is_transfer:
        transfer_account_id = txn.get("transfer_account_id")
        transfer_txn_id = txn.get("transfer_transaction_id")
        other_acct = sync.account(transfer_account_id) if transfer_account_id else None
        bucket = "transfer_paired" if (other_acct and other_acct.lm_type != "skipped") else "transfer_one_sided"

        meta = _base_meta(txn)
        if transfer_txn_id:
            meta["ynab_paired_id"] = transfer_txn_id

        if options.since and txn_date < options.since:
            return ClassifiedTxn(ynab_id=ynab_id, bucket="skipped_before_since")

        insert = _make_insert(
            txn,
            acct_entry=acct_entry,
            currency=currency,
            category_id=sync.special_cat_id("payment_transfer"),
            custom_metadata=meta,
        )
        return ClassifiedTxn(ynab_id=ynab_id, bucket=bucket, insert=insert)

    # ── Apply --since filter (before building insert for non-opening-balance txns) ──
    if options.since and txn_date < options.since:
        return ClassifiedTxn(ynab_id=ynab_id, bucket="skipped_before_since")

    # ── Category-based classification ─────────────────────────────────────────
    is_balance_adj = bool(payee_name and _BALANCE_ADJ_KEYWORD in payee_name)

    if is_balance_adj:
        bucket = "balance_adjustment"
        lm_cat_id: Optional[int] = None
        meta = _base_meta(txn)

    elif cat_name == _CAT_INFLOW:
        bucket = "income"
        lm_cat_id = sync.lm_category_id(cat_id) if cat_id else None
        meta = _base_meta(txn)

    elif cat_name == _CAT_UNCATEGORIZED or (
        cat_id and cat_id == sync.ynab_internal_cat("uncategorized")
    ):
        bucket = "uncategorized"
        lm_cat_id = None
        meta = {**_base_meta(txn), "ynab_uncategorized": True}

    elif cat_name == _CAT_DEFERRED:
        if options.deferred_income_as == "income":
            bucket = "income"
            lm_cat_id = sync.lm_category_id(cat_id) if cat_id else None
            meta = _base_meta(txn)
        elif options.deferred_income_as == "uncategorized":
            bucket = "uncategorized"
            lm_cat_id = None
            meta = {**_base_meta(txn), "ynab_uncategorized": True}
        elif options.deferred_income_as == "skip":
            return ClassifiedTxn(
                ynab_id=ynab_id, bucket="skipped_before_since",
                note="deferred income skipped per --deferred-income-as skip",
            )
        else:
            return ClassifiedTxn(
                ynab_id=ynab_id, bucket="needs_decision",
                note="Deferred Income SubCategory — use --deferred-income-as to resolve",
            )

    else:
        # Normal spending / categorized income
        bucket = "spending"
        lm_cat_id = sync.lm_category_id(cat_id) if cat_id else None
        meta = _base_meta(txn)

    # Off-budget account override (not for transfers — already returned above)
    if src_account_off_budget:
        lm_cat_id = sync.special_cat_id("tracking_off_budget")
        bucket = "tracking"

    insert = _make_insert(
        txn,
        acct_entry=acct_entry,
        currency=currency,
        category_id=lm_cat_id,
        custom_metadata=meta,
    )
    return ClassifiedTxn(ynab_id=ynab_id, bucket=bucket, insert=insert)


def build_transaction_plan(
    ynab_txns: list[dict[str, Any]],
    ynab_accounts: list[dict[str, Any]],
    *,
    sync: SyncState,
    options: TxnImportOptions,
) -> TransactionPlan:
    """Classify YNAB transactions and produce LM insert objects. Pure — no I/O."""
    accts_by_id = {a["id"]: a for a in ynab_accounts}
    items: list[ClassifiedTxn] = []
    split_requests: list[SplitRequest] = []

    for txn in ynab_txns:
        classified = _classify_txn(txn, accts_by_id, sync, options)
        items.append(classified)
        if classified.split_children is not None:
            non_deleted_subs = [s for s in (txn.get("subtransactions") or [])
                                if not s.get("deleted")]
            split_requests.append(SplitRequest(
                ynab_parent_id=classified.ynab_id,
                child_transactions=classified.split_children,
                ynab_sub_ids=[s["id"] for s in non_deleted_subs],
            ))

    counts: dict[str, int] = {b: 0 for b in BUCKETS}
    counts["split_children"] = sum(len(sr.child_transactions) for sr in split_requests)
    for item in items:
        if item.bucket in counts:
            counts[item.bucket] += 1

    needs_decision = [i for i in items if i.bucket == "needs_decision"]

    return TransactionPlan(
        items=items,
        counts=counts,
        needs_decision=needs_decision,
        split_requests=split_requests,
    )


def preflight_check(
    ynab_txns: list[dict[str, Any]],
    ynab_accounts: list[dict[str, Any]],
    sync: SyncState,
    pending_special_cats: set[str] = frozenset(),
) -> list[str]:
    """Return list of error strings; empty list means pre-flight passed.

    *pending_special_cats* lists special-category keys that would be created later in the same
    run (dry-run preview), so they're treated as satisfied rather than aborting."""
    errors: list[str] = []
    accts_by_id = {a["id"]: a for a in ynab_accounts}

    has_off_budget = any(not a.get("on_budget", True) for a in ynab_accounts
                         if not a.get("deleted"))
    has_splits = any(
        t.get("category_name") == _CAT_SPLIT and t.get("subtransactions")
        for t in ynab_txns if not t.get("deleted")
    )

    def _missing_special(key: str) -> bool:
        return sync.special_cat_id(key) is None and key not in pending_special_cats

    if _missing_special("payment_transfer"):
        errors.append("Special category 'Payment, Transfer' not in sync_state — run import categories first")
    if has_off_budget and _missing_special("tracking_off_budget"):
        errors.append("Special category 'Tracking (off-budget)' required but not in sync_state")
    if has_splits and _missing_special("incomplete_split"):
        errors.append("Special category 'Incomplete Split' required for split transactions but not in sync_state")

    # Check every non-deleted txn's account is in sync_state
    seen_account_errors: set[str] = set()
    for txn in ynab_txns:
        if txn.get("deleted"):
            continue
        account_id = txn.get("account_id", "")
        if account_id in seen_account_errors:
            continue
        acct = accts_by_id.get(account_id)
        if acct and acct.get("deleted"):
            continue
        entry = sync.account(account_id)
        transfer_account_id = txn.get("transfer_account_id")
        if entry is None:
            # Transfers to unmigrated accounts are OK (one-sided); non-transfers are errors
            if not transfer_account_id:
                acct_name = (accts_by_id.get(account_id) or {}).get("name", account_id)
                errors.append(f"Account '{acct_name}' ({account_id}) has transactions but is not in sync_state")
                seen_account_errors.add(account_id)

    return errors


# ── Update plan types and builder ─────────────────────────────────────────────

UPDATE_BUCKETS = (
    "update_regular",
    "update_split_inplace",
    "update_split_structural",
    "skipped_no_change",          # ynab_hash unchanged — nothing to push
    "skipped_derived_unchanged",  # ynab_hash changed but only in unmapped fields — refresh hash, no PUT
    "baseline",                   # no stored ynab_hash (fresh index) — establish baseline, no PUT
)

UPDATE_ACTION_BUCKETS = (
    "update_regular",
    "update_split_inplace",
    "update_split_structural",
)


@dataclass
class TxnUpdateItem:
    ynab_id: str
    lm_id: int
    bucket: str
    payload: Optional[dict[str, Any]] = None
    parent_payload: Optional[dict[str, Any]] = None
    child_updates: Optional[list[tuple[int, dict[str, Any]]]] = None
    new_children: Optional[list[SplitTransactionObject]] = None
    ynab_sub_ids: Optional[list[str]] = None
    old_sub_ids: Optional[list[str]] = None
    new_ynab_hash: str = ""
    new_derived_hash: str = ""
    note: str = ""


@dataclass
class TransactionUpdatePlan:
    items: list[TxnUpdateItem]
    counts: dict[str, int]


def insert_lm_fields(
    insert: InsertTransactionObject,
    split_children: Optional[list[SplitTransactionObject]] = None,
) -> dict[str, Any]:
    """Extract the LM payload fields (for hashing/diffing) from the insert we'd send.

    The YNAB-derived side. Pairs with sinks.lm_fields_from_transaction (the live-LM side):
    both feed transactions.lm_payload_equal, which owns all LM-side normalization (recurring
    notes, "[No Payee]", split-child inheritance) so it can change with zero re-baselining.
    """
    d = insert.model_dump(mode="json", exclude_none=False)
    fields: dict[str, Any] = {
        "date": d.get("date"), "amount": d.get("amount"), "payee": d.get("payee"),
        "category_id": d.get("category_id"),
        "notes": d.get("notes"),
        "status": d.get("status"),
    }
    if split_children:
        fields["split_children"] = [
            {k: v for k, v in c.model_dump(mode="json").items()
             if k in ("amount", "category_id", "notes", "payee")}
            for c in split_children
        ]
    return fields


def compute_insert_lm_hash(
    insert: InsertTransactionObject,
    split_children: Optional[list[SplitTransactionObject]] = None,
) -> str:
    """Compute the `derived_hash` from the InsertTransactionObject we'd send.

    Purely a function of our own YNAB→LM derivation; never reads LM back.
    """
    from sync_state import compute_lm_hash
    return compute_lm_hash(insert_lm_fields(insert, split_children))


def _parent_only_payload(insert: InsertTransactionObject) -> dict[str, Any]:
    """PUT payload for the split parent (parent-level fields only, no account/currency)."""
    d = insert.model_dump(mode="json", exclude_none=True)
    for k in ("external_id", "manual_account_id", "plaid_account_id", "currency"):
        d.pop(k, None)
    return d


def build_transaction_update_plan(
    ynab_txns: list[dict[str, Any]],
    ynab_accounts: list[dict[str, Any]],
    *,
    sync: SyncState,
    options: TxnImportOptions,
) -> TransactionUpdatePlan:
    """Pure, offline. For each YNAB txn already in sync.transactions, decide whether an LM
    update is needed and what kind — using only the stored ``ynab_hash`` (did YNAB change?)
    and ``derived_hash`` (did that change touch a field we map to LM?). Never reads LM; the
    explicit ``reconcile`` step is what compares against live LM data. No I/O."""
    from sync_state import compute_ynab_hash
    accts_by_id = {a["id"]: a for a in ynab_accounts}
    items: list[TxnUpdateItem] = []

    for txn in ynab_txns:
        ynab_id = txn["id"]
        entry = sync.txn(ynab_id)
        if entry is None:
            continue
        if txn.get("deleted"):
            continue

        current_ynab_hash = compute_ynab_hash(txn)
        classified = _classify_txn(txn, accts_by_id, sync, options)
        if classified.insert is None:
            continue

        new_derived_hash = compute_insert_lm_hash(
            classified.insert, classified.split_children)
        stored_ynab = entry.ynab_hash
        stored_derived = getattr(entry, "derived_hash", "")

        if stored_ynab == "":
            # Fresh index (no YNAB baseline): assume LM already reflects YNAB and record
            # the baseline without writing. Genuine drift is surfaced by `reconcile`.
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="baseline",
                new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
            ))
            continue

        if stored_ynab == current_ynab_hash:
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_no_change",
                new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
            ))
            continue

        # YNAB changed. If the YNAB-derived LM payload is unchanged, the change touched
        # only fields we don't map to LM — refresh the baseline, issue no PUT.
        if stored_derived != "" and stored_derived == new_derived_hash:
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="skipped_derived_unchanged",
                new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
                note="YNAB changed only in unmapped fields",
            ))
            continue

        is_split = classified.split_children is not None

        if not is_split:
            insert_dict = classified.insert.model_dump(mode="json", exclude_none=True)
            for k in ("external_id", "manual_account_id", "plaid_account_id", "currency"):
                insert_dict.pop(k, None)
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_regular",
                payload=insert_dict,
                new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
            ))
            continue

        subs = [s for s in (txn.get("subtransactions") or []) if not s.get("deleted")]
        current_sub_ids = {s["id"] for s in subs}
        known_sub_ids = {sid for sid in current_sub_ids
                         if sync.split_child_lm_id(sid) is not None}

        if not known_sub_ids:
            parent_payload = _parent_only_payload(classified.insert)
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_split_structural",
                parent_payload=parent_payload,
                new_children=classified.split_children,
                ynab_sub_ids=[s["id"] for s in subs],
                old_sub_ids=[],
                new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
                note="no child map (post-rebuild) — unsplit+resplit",
            ))
            continue

        if current_sub_ids != known_sub_ids:
            parent_payload = _parent_only_payload(classified.insert)
            items.append(TxnUpdateItem(
                ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_split_structural",
                parent_payload=parent_payload,
                new_children=classified.split_children,
                ynab_sub_ids=[s["id"] for s in subs],
                old_sub_ids=list(known_sub_ids),
                new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
                note="sub structure changed",
            ))
            continue

        # Non-structural: per-child updates
        child_updates: list[tuple[int, dict[str, Any]]] = []
        for sub, lm_child in zip(subs, classified.split_children or []):
            child_lm_id = sync.split_child_lm_id(sub["id"])
            if child_lm_id is None:
                continue
            child_payload: dict[str, Any] = {
                "amount": lm_child.amount,
                "category_id": lm_child.category_id,
                "notes": lm_child.notes,
                "payee": lm_child.payee,
            }
            if lm_child.date:
                child_payload["date"] = lm_child.date.isoformat()
            child_payload = {k: v for k, v in child_payload.items() if v is not None}
            child_updates.append((child_lm_id, child_payload))

        parent_payload = _parent_only_payload(classified.insert)
        items.append(TxnUpdateItem(
            ynab_id=ynab_id, lm_id=entry.lm_id, bucket="update_split_inplace",
            parent_payload=parent_payload,
            child_updates=child_updates,
            new_ynab_hash=current_ynab_hash, new_derived_hash=new_derived_hash,
        ))

    counts = {b: sum(1 for i in items if i.bucket == b) for b in UPDATE_BUCKETS}
    return TransactionUpdatePlan(items=items, counts=counts)


# ── Reconcile: compare YNAB-derived payload against a live LM snapshot ─────────
#
# This is the ONLY place that compares against LM-read data, and the ONLY place LM-side
# normalization lives. It works on full field values (not hashes), so the rules below can
# change freely with zero re-baselining. The snapshot dicts come from
# sinks.lm_fields_from_transaction; the derived dicts from insert_lm_fields.

_LM_NO_PAYEE = "[No Payee]"


def _norm_snapshot_payee(p: Any) -> Any:
    """LM stores the literal "[No Payee]" (and sometimes "") where we send None."""
    return None if p in (None, "", _LM_NO_PAYEE) else p


def _child_norm_derived(c: dict[str, Any]) -> dict[str, Any]:
    return {"amount": c.get("amount"), "category_id": c.get("category_id"),
            "payee": c.get("payee") or None, "notes": c.get("notes") or None}


def _child_norm_snapshot(c: dict[str, Any], parent_payee: Any,
                         parent_notes: Any) -> dict[str, Any]:
    # A split child whose snapshot payee/notes equals the parent's is *inheriting* it
    # (a v2 display quirk); our re-derived children carry None there. Normalize to match.
    payee = _norm_snapshot_payee(c.get("payee"))
    if payee == _norm_snapshot_payee(parent_payee):
        payee = None
    notes = c.get("notes")
    if notes == parent_notes:
        notes = None
    return {"amount": c.get("amount"), "category_id": c.get("category_id"),
            "payee": payee, "notes": notes or None}


def _child_key(c: dict[str, Any]) -> str:
    import json as _j
    return _j.dumps(c, sort_keys=True)


def lm_payload_diffs(derived: dict[str, Any],
                     snapshot: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Field-level differences between our YNAB-derived payload and an LM snapshot.

    Returns a list of (field, snapshot_value, derived_value). Empty list ⇒ LM matches YNAB.
    Normalization rules (all LM-side quirks live here):
      - recurring-linked txns: exclude `notes`/`payee` (v2 returns inherited display values)
      - "[No Payee]" / "" ≡ None
      - split children inheriting the parent's payee/notes ≡ None (compared as a multiset;
        `amount`/`category_id` compared strictly)
    """
    recurring = bool(snapshot.get("recurring"))
    diffs: list[tuple[str, Any, Any]] = []

    for k in ("date", "amount", "category_id", "status"):
        if derived.get(k) != snapshot.get(k):
            diffs.append((k, snapshot.get(k), derived.get(k)))

    if not recurring:
        snap_payee = _norm_snapshot_payee(snapshot.get("payee"))
        if (derived.get("payee") or None) != snap_payee:
            diffs.append(("payee", snapshot.get("payee"), derived.get("payee")))
        if (derived.get("notes") or None) != (snapshot.get("notes") or None):
            diffs.append(("notes", snapshot.get("notes"), derived.get("notes")))

    dch = derived.get("split_children") or []
    sch = snapshot.get("split_children") or []
    if len(dch) != len(sch):
        diffs.append(("split_children", sch, dch))
    elif dch:
        parent_payee = snapshot.get("payee")
        parent_notes = snapshot.get("notes")
        dnorm = sorted((_child_norm_derived(c) for c in dch), key=_child_key)
        snorm = sorted((_child_norm_snapshot(c, parent_payee, parent_notes) for c in sch),
                       key=_child_key)
        if dnorm != snorm:
            diffs.append(("split_children", snorm, dnorm))

    return diffs


def lm_payload_equal(derived: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    return not lm_payload_diffs(derived, snapshot)


@dataclass
class ReconcileReport:
    examined: int                                    # mapped txns found in the snapshot
    unmatched: int                                   # mapped txns NOT found in the snapshot
    field_tally: dict[str, int]                      # field -> count of txns differing in it
    examples: dict[str, list[tuple[str, Any, Any]]]  # field -> [(ynab_id, snap, derived), ...]
    differing_ids: list[str]                         # ynab_ids where LM differs from YNAB


def build_reconcile_report(
    ynab_txns: list[dict[str, Any]],
    ynab_accounts: list[dict[str, Any]],
    snapshot_by_ynab_id: dict[str, dict[str, Any]],
    *,
    sync: SyncState,
    options: TxnImportOptions,
) -> ReconcileReport:
    """Pure. Compare every mapped YNAB txn's derived payload against the LM snapshot
    (dicts from sinks.lm_fields_from_transaction, keyed by ynab_id)."""
    accts_by_id = {a["id"]: a for a in ynab_accounts}
    field_tally: dict[str, int] = {}
    examples: dict[str, list[tuple[str, Any, Any]]] = {}
    differing_ids: list[str] = []
    examined = unmatched = 0

    for txn in ynab_txns:
        ynab_id = txn["id"]
        if sync.txn(ynab_id) is None or txn.get("deleted"):
            continue
        snapshot = snapshot_by_ynab_id.get(ynab_id)
        if snapshot is None:
            unmatched += 1
            continue
        classified = _classify_txn(txn, accts_by_id, sync, options)
        if classified.insert is None:
            continue
        examined += 1
        derived = insert_lm_fields(classified.insert, classified.split_children)
        diffs = lm_payload_diffs(derived, snapshot)
        if diffs:
            differing_ids.append(ynab_id)
            for field_name, old, new in diffs:
                field_tally[field_name] = field_tally.get(field_name, 0) + 1
                ex = examples.setdefault(field_name, [])
                if len(ex) < 10:
                    ex.append((ynab_id, old, new))

    return ReconcileReport(examined=examined, unmatched=unmatched,
                           field_tally=field_tally, examples=examples,
                           differing_ids=differing_ids)
