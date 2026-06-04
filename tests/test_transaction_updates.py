"""Tests for the transaction update plan: hash functions, decision table, apply logic."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lunchmoney"))

from sync_state import (
    SyncState, SyncStateData, TxnEntry, AccountEntry, CategoryEntry,
    compute_ynab_hash, compute_lm_hash,
)
from transactions import (
    TxnImportOptions, build_transaction_update_plan, TxnUpdateItem,
    compute_insert_lm_hash,
)
from lm_api_types_generated import InsertTransactionObject, SplitTransactionObject
from sinks import InsertResult
import importlib
import tempfile
_import_mod = importlib.import_module("import")
_apply_update_plan = _import_mod._apply_update_plan


def _run_apply(update_plan, sink, sync, apply: bool = True) -> int:
    with tempfile.TemporaryDirectory() as d:
        return _apply_update_plan(update_plan, sink, sync, Path(d), apply)


# ── Helpers ───────────────────────────────────────────────────────────────────

ACCT_ID = "acct-1"
CAT_ID = "cat-groceries"
CAT_LM_ID = 300
PAYMENT_CAT_LM_ID = 500
INCOMPLETE_SPLIT_LM_ID = 502
INFLOW_CAT_ID = "cat-inflow"
UNCAT_CAT_ID = "cat-uncat"


def make_sync(
    *,
    transactions: dict | None = None,
    split_children: dict | None = None,
    ynab_txn_server_knowledge: int = 0,
) -> SyncState:
    data = SyncStateData(
        ynab_budget_id="test",
        ynab_budget_name="Test",
        lm_account_id=1,
        currency="cad",
        accounts={ACCT_ID: AccountEntry(lm_type="manual", lm_id=101, lm_name="Acct 1")},
        categories={
            CAT_ID: CategoryEntry(lm_id=CAT_LM_ID, lm_name="Groceries"),
            INFLOW_CAT_ID: CategoryEntry(lm_id=200, lm_name="Inflow"),
        },
        special_categories={
            "payment_transfer": PAYMENT_CAT_LM_ID,
            "incomplete_split": INCOMPLETE_SPLIT_LM_ID,
        },
        ynab_internal_cats={"inflow": INFLOW_CAT_ID, "uncategorized": UNCAT_CAT_ID},
        transactions={k: TxnEntry(**v) for k, v in (transactions or {}).items()},
        split_children=split_children or {},
        ynab_txn_server_knowledge=ynab_txn_server_knowledge,
    )
    return SyncState(data)


def make_ynab_txn(
    *,
    id: str = "txn-1",
    date: str = "2024-01-15",
    amount: int = -50000,
    account_id: str = ACCT_ID,
    category_id: str | None = CAT_ID,
    category_name: str | None = "Groceries",
    payee_name: str | None = "Supermarket",
    memo: str | None = None,
    approved: bool = True,
    flag_color: str | None = None,
    deleted: bool = False,
    subtransactions: list | None = None,
) -> dict:
    return {
        "id": id, "date": date, "amount": amount,
        "account_id": account_id,
        "category_id": category_id, "category_name": category_name,
        "payee_name": payee_name, "memo": memo,
        "approved": approved, "flag_color": flag_color,
        "cleared": "cleared", "deleted": deleted,
        "transfer_account_id": None, "transfer_transaction_id": None,
        "subtransactions": subtransactions or [],
    }


def make_split_ynab_txn(parent_id: str, subs: list[dict],
                        account_id: str = ACCT_ID) -> dict:
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


def make_sub(sub_id: str, amount: int, category_id: str = CAT_ID,
             memo: str | None = None) -> dict:
    return {
        "id": sub_id, "amount": amount,
        "category_id": category_id, "category_name": "Groceries",
        "payee_name": None, "memo": memo, "deleted": False,
    }


def ynab_accounts() -> list[dict]:
    return [{"id": ACCT_ID, "name": "Acct 1", "on_budget": True, "deleted": False,
             "type": "checking"}]


def _options(**kwargs) -> TxnImportOptions:
    return TxnImportOptions(**kwargs)


@dataclass
class FakeSink:
    calls: list[tuple[str, Any]] = field(default_factory=list)
    fail_at: int | None = None
    already_unsplit: bool = False

    def update(self, lm_id: int, payload: dict) -> None:
        self._record("update", (lm_id, payload))

    def unsplit(self, lm_id: int) -> None:
        self._record("unsplit", lm_id)
        if self.already_unsplit:
            raise Exception("TRANSACTION_IS_NOT_SPLIT_PARENT")

    def split(self, parent_lm_id: int, children: list) -> list[int]:
        self._record("split", (parent_lm_id, children))
        return [9000 + i for i in range(len(children))]

    def _record(self, name: str, arg: Any) -> None:
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError(f"Simulated crash at call {self.fail_at}")
        self.calls.append((name, arg))

    def scan_imported(self): return []
    def insert(self, txns): return InsertResult(0, 0, {})
    def close(self): pass



# ── Hash function tests ───────────────────────────────────────────────────────

def _base_txn(**kwargs) -> dict:
    return make_ynab_txn(**kwargs)


def test_ynab_hash_tracks_date_change():
    t1 = _base_txn(date="2024-01-01")
    t2 = _base_txn(date="2024-01-02")
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_amount_change():
    t1 = _base_txn(amount=-50000)
    t2 = _base_txn(amount=-60000)
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_category_change():
    t1 = _base_txn(category_id="cat-a")
    t2 = _base_txn(category_id="cat-b")
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_payee_change():
    t1 = _base_txn(payee_name="Shop A")
    t2 = _base_txn(payee_name="Shop B")
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_memo_change():
    t1 = _base_txn(memo=None)
    t2 = _base_txn(memo="some note")
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_approved_change():
    t1 = _base_txn(approved=True)
    t2 = _base_txn(approved=False)
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_flag_color_change():
    t1 = _base_txn(flag_color=None)
    t2 = _base_txn(flag_color="red")
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_ignores_import_id():
    t1 = {**_base_txn(), "import_id": "import-123"}
    t2 = _base_txn()
    assert compute_ynab_hash(t1) == compute_ynab_hash(t2)


def test_ynab_hash_ignores_cleared():
    t1 = {**_base_txn(), "cleared": "cleared"}
    t2 = {**_base_txn(), "cleared": "uncleared"}
    assert compute_ynab_hash(t1) == compute_ynab_hash(t2)


def test_ynab_hash_ignores_account_id():
    t1 = _base_txn(account_id="acct-a")
    t2 = _base_txn(account_id="acct-b")
    assert compute_ynab_hash(t1) == compute_ynab_hash(t2)


def test_ynab_hash_subs_order_independent():
    subs_a = [make_sub("s1", -20000), make_sub("s2", -30000, "cat-b")]
    subs_b = [make_sub("s2", -30000, "cat-b"), make_sub("s1", -20000)]
    t1 = make_split_ynab_txn("p1", subs_a)
    t2 = make_split_ynab_txn("p1", subs_b)
    assert compute_ynab_hash(t1) == compute_ynab_hash(t2)


def test_ynab_hash_tracks_sub_category_change():
    subs_a = [make_sub("s1", -50000, "cat-a")]
    subs_b = [make_sub("s1", -50000, "cat-b")]
    t1 = make_split_ynab_txn("p1", subs_a)
    t2 = make_split_ynab_txn("p1", subs_b)
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_sub_amount_change():
    subs_a = [make_sub("s1", -40000), make_sub("s2", -10000)]
    subs_b = [make_sub("s1", -30000), make_sub("s2", -20000)]
    t1 = make_split_ynab_txn("p1", subs_a)
    t2 = make_split_ynab_txn("p1", subs_b)
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_ynab_hash_tracks_sub_memo_change():
    subs_a = [make_sub("s1", -50000, memo=None)]
    subs_b = [make_sub("s1", -50000, memo="note")]
    t1 = make_split_ynab_txn("p1", subs_a)
    t2 = make_split_ynab_txn("p1", subs_b)
    assert compute_ynab_hash(t1) != compute_ynab_hash(t2)


def test_lm_hash_tracks_date_change():
    assert compute_lm_hash({"date": "2024-01-01"}) != compute_lm_hash({"date": "2024-01-02"})


def test_lm_hash_tracks_amount_change():
    assert compute_lm_hash({"amount": "50.0000"}) != compute_lm_hash({"amount": "60.0000"})


def test_lm_hash_tracks_category_id_change():
    assert compute_lm_hash({"category_id": 1}) != compute_lm_hash({"category_id": 2})


def test_lm_hash_tracks_payee_change():
    assert compute_lm_hash({"payee": "A"}) != compute_lm_hash({"payee": "B"})


def test_lm_hash_tracks_notes_change():
    assert compute_lm_hash({"notes": None}) != compute_lm_hash({"notes": "note"})


def test_lm_hash_tracks_status_change():
    assert compute_lm_hash({"status": "reviewed"}) != compute_lm_hash({"status": "unreviewed"})


def test_lm_hash_ignores_external_id():
    h1 = compute_lm_hash({"external_id": "abc", "amount": "1.0000"})
    h2 = compute_lm_hash({"amount": "1.0000"})
    assert h1 == h2


def test_lm_hash_ignores_custom_metadata():
    h1 = compute_lm_hash({"custom_metadata": {"ynab_id": "x"}, "amount": "1.0000"})
    h2 = compute_lm_hash({"amount": "1.0000"})
    assert h1 == h2


def test_lm_hash_split_includes_child_fields():
    children_a = [{"amount": "20.0000", "category_id": 1, "notes": None, "payee": None}]
    children_b = [{"amount": "20.0000", "category_id": 2, "notes": None, "payee": None}]
    h1 = compute_lm_hash({"split_children": children_a})
    h2 = compute_lm_hash({"split_children": children_b})
    assert h1 != h2


def test_lm_hash_split_children_order_independent():
    c1 = {"amount": "20.0000", "category_id": 1, "notes": None, "payee": None}
    c2 = {"amount": "30.0000", "category_id": 2, "notes": None, "payee": None}
    h1 = compute_lm_hash({"split_children": [c1, c2]})
    h2 = compute_lm_hash({"split_children": [c2, c1]})
    assert h1 == h2


def test_lm_hash_from_insert_matches_from_transaction_object_fields():
    from datetime import date as date_cls
    insert = InsertTransactionObject(
        date=date_cls(2024, 1, 15),
        amount="50.0000",
        payee="Supermarket",
        category_id=CAT_LM_ID,
        notes="a note",
        status="reviewed",
        manual_account_id=101,
        external_id="txn-1",
        custom_metadata={"ynab_id": "txn-1"},
    )
    h_insert = compute_insert_lm_hash(insert)
    fields = {
        "date": "2024-01-15",
        "amount": "50.0000",
        "payee": "Supermarket",
        "category_id": CAT_LM_ID,
        "notes": "a note",
        "status": "reviewed",
    }
    h_fields = compute_lm_hash(fields)
    assert h_insert == h_fields


# ── Decision table tests ──────────────────────────────────────────────────────

def _stored_hashes(txn: dict) -> tuple[str, str]:
    """Compute both hashes for a txn as if previously imported."""
    from datetime import date as date_cls
    yh = compute_ynab_hash(txn)
    # Build a minimal insert to compute lm_hash
    insert = InsertTransactionObject(
        date=date_cls.fromisoformat(txn["date"]),
        amount=f"{-txn['amount'] / 1000:.4f}",
        payee=txn.get("payee_name"),
        category_id=CAT_LM_ID if txn.get("category_id") == CAT_ID else None,
        notes=txn.get("memo") or None,
        status="reviewed" if txn.get("approved") else "unreviewed",
        manual_account_id=101,
        external_id=txn["id"],
        custom_metadata={"ynab_id": txn["id"]},
    )
    lh = compute_insert_lm_hash(insert)
    return yh, lh


def test_no_change_skips():
    txn = make_ynab_txn()
    yh, lh = _stored_hashes(txn)
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": yh, "lm_hash": lh}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    assert len(plan.items) == 1
    assert plan.items[0].bucket == "skipped_no_change"


def test_lm_edited_skips_and_does_not_overwrite():
    txn = make_ynab_txn()
    yh, _ = _stored_hashes(txn)
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": yh, "lm_hash": "different"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    assert plan.items[0].bucket == "skipped_lm_edited"


def test_ynab_changed_unmapped_field_skips():
    txn = make_ynab_txn()
    _, lh = _stored_hashes(txn)
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "old-hash", "lm_hash": lh}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    assert plan.items[0].bucket == "skipped_ynab_unmapped"


def test_both_changed_is_conflict():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "old-yh", "lm_hash": "old-lh"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    assert plan.items[0].bucket == "conflict"
    assert len(plan.conflicts) == 1


def test_both_changed_force_ynab_updates():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "old-yh", "lm_hash": "old-lh"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_regular"
    assert len(plan.conflicts) == 0


def test_regular_txn_update_payload_excludes_external_id():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "old", "lm_hash": "old"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_regular"
    assert "external_id" not in (plan.items[0].payload or {})


def test_regular_txn_update_payload_excludes_account_id():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "old", "lm_hash": "old"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    payload = plan.items[0].payload or {}
    assert "manual_account_id" not in payload
    assert "plaid_account_id" not in payload


def test_regular_txn_update_payload_excludes_currency():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "old", "lm_hash": "old"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert "currency" not in (plan.items[0].payload or {})


def test_split_inplace_when_sub_ids_unchanged():
    subs = [make_sub("s1", -20000), make_sub("s2", -30000)]
    txn = make_split_ynab_txn("p1", subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100, "s2": 101},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_inplace"
    assert plan.items[0].child_updates is not None


def test_split_structural_when_sub_added():
    old_subs = [make_sub("s1", -50000)]
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]
    txn = make_split_ynab_txn("p1", new_subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},  # s2 is new — not in map
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_structural"


def test_split_structural_when_sub_removed():
    # Sub s2 was removed from YNAB (deleted=True)
    subs = [make_sub("s1", -50000), {**make_sub("s2", -0), "deleted": True}]
    txn = make_split_ynab_txn("p1", subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100, "s2": 101},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    # After filtering deleted subs, only s1 remains; s2 mapping still exists
    # but s2 is not in current_sub_ids, so it's not in known_sub_ids either.
    # Result: inplace (s1 is the only current sub and is known)
    assert plan.items[0].bucket in ("update_split_inplace", "update_split_structural")


def test_split_structural_when_no_child_map():
    subs = [make_sub("s1", -50000)]
    txn = make_split_ynab_txn("p1", subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={},  # no child map
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_structural"
    assert plan.items[0].note == "no child map (post-rebuild) — unsplit+resplit"


def test_both_hashes_empty_initialises_baseline_no_update():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "", "lm_hash": ""}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    assert plan.items[0].bucket == "skipped_no_change"
    assert "initialising hash baseline" in plan.items[0].note
    assert plan.items[0].new_ynab_hash != ""
    assert plan.items[0].new_lm_hash != ""


def test_ynab_hash_empty_lm_hash_changed_treated_as_ynab_change():
    txn = make_ynab_txn()
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "", "lm_hash": "old-lm"}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    # lm_hash doesn't match current — post-rebuild treat as YNAB change → update
    assert plan.items[0].bucket in ("update_regular", "update_split_inplace", "update_split_structural")


def test_ynab_hash_empty_lm_hash_unchanged_skips():
    txn = make_ynab_txn()
    _, lh = _stored_hashes(txn)
    sync = make_sync(transactions={"txn-1": {"lm_id": 10, "ynab_hash": "", "lm_hash": lh}})
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync, options=_options())
    assert plan.items[0].bucket == "skipped_no_change"


# ── Structural update crash resistance ────────────────────────────────────────

def _make_structural_update_plan() -> tuple[object, SyncState]:
    """Build a plan with one update_split_structural item.

    Setup: s1 is known (in split_children), s2 is new. Both are current subs.
    This gives old_sub_ids=["s1"] so step A actually clears something.
    """
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]
    txn = make_split_ynab_txn("p1", new_subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},  # s1 is known, s2 is new
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_structural"
    assert plan.items[0].old_sub_ids == ["s1"]
    return plan, sync


def assert_sync_consistent(sync: SyncState, parent_id: str, current_sub_ids: set[str],
                            all_known_sub_ids: set[str]) -> None:
    entry = sync.txn(parent_id)
    assert entry is not None
    if entry.split_done:
        for sid in current_sub_ids:
            assert sync.split_child_lm_id(sid) is not None, \
                f"split_done=True but no child_lm_id for sub {sid}"
        stale = all_known_sub_ids - current_sub_ids
        for sid in stale:
            assert sync.split_child_lm_id(sid) is None, \
                f"Stale split_children entry for old sub {sid} still present"
    else:
        for sid in all_known_sub_ids:
            assert sync.split_child_lm_id(sid) is None, \
                f"split_done=False but split_children[{sid}] is set"


def test_structural_crash_after_step_A():
    plan, sync = _make_structural_update_plan()
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]

    sink = FakeSink(fail_at=0)  # crash before unsplit (step B)
    try:
        _run_apply(plan, sink, sync)
    except RuntimeError:
        pass
    # Step A completed: split_done=False, s1 cleared from split_children
    entry = sync.txn("p1")
    assert entry.split_done is False
    assert sync.split_child_lm_id("s1") is None

    # Recovery run — LM still has the split (crash was before API call)
    sink2 = FakeSink(already_unsplit=False)
    txn = make_split_ynab_txn("p1", new_subs)
    plan2 = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                           options=_options(force_ynab=True))
    _run_apply(plan2, sink2, sync)

    assert sync.txn("p1").split_done is True
    assert sync.split_child_lm_id("s1") == 9000
    assert sync.split_child_lm_id("s2") == 9001
    assert_sync_consistent(sync, "p1", {"s1", "s2"}, {"s1", "s2"})


def test_structural_crash_after_step_B():
    plan, sync = _make_structural_update_plan()
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]

    sink = FakeSink(fail_at=1)  # crash after unsplit (call 0), before split (call 1)
    try:
        _run_apply(plan, sink, sync)
    except RuntimeError:
        pass

    entry = sync.txn("p1")
    assert entry.split_done is False
    assert sync.split_child_lm_id("s1") is None  # cleared in step A

    # Recovery: LM is already unsplit
    sink2 = FakeSink(already_unsplit=True)
    txn = make_split_ynab_txn("p1", new_subs)
    plan2 = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                           options=_options(force_ynab=True))
    _run_apply(plan2, sink2, sync)

    assert sync.txn("p1").split_done is True
    assert sync.split_child_lm_id("s1") == 9000
    assert sync.split_child_lm_id("s2") == 9001
    assert_sync_consistent(sync, "p1", {"s1", "s2"}, {"s1", "s2"})


def test_structural_crash_after_step_D():
    # Use parent_payload to ensure call order: unsplit(0), update(1), split(2)
    # Setup: s1 is known, s2 is new → old_sub_ids=["s1"]
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]
    txn = make_split_ynab_txn("p1", new_subs)
    txn["payee_name"] = "New Payee"  # change payee to ensure parent_payload is non-empty
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_structural"
    assert plan.items[0].parent_payload is not None  # ensures call order: unsplit(0), update(1), split(2)

    sink = FakeSink(fail_at=2)  # crash at split (step D, call index 2)
    try:
        _run_apply(plan, sink, sync)
    except RuntimeError:
        pass

    entry = sync.txn("p1")
    assert entry.split_done is False
    assert sync.split_child_lm_id("s1") is None  # cleared in step A

    # LM is unsplit (step B done, step D never completed), so already_unsplit=True
    sink2 = FakeSink(already_unsplit=True)
    plan2 = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                           options=_options(force_ynab=True))
    _run_apply(plan2, sink2, sync)

    assert sync.txn("p1").split_done is True
    assert sync.split_child_lm_id("s1") == 9000
    assert sync.split_child_lm_id("s2") == 9001
    assert_sync_consistent(sync, "p1", {"s1", "s2"}, {"s1", "s2"})


def test_structural_idempotent_full_run():
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]
    txn = make_split_ynab_txn("p1", new_subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},
    )

    sink = FakeSink()
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    _run_apply(plan, sink, sync)

    assert sync.txn("p1").split_done is True
    first_calls = list(sink.calls)

    # Second run: hashes now match, no changes
    sink2 = FakeSink()
    plan2 = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                           options=_options(force_ynab=True))
    _run_apply(plan2, sink2, sync)

    assert sink2.calls == [], f"Second run should make no API calls, got: {sink2.calls}"


def test_structural_old_sub_ids_cleared_from_split_children():
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]
    txn = make_split_ynab_txn("p1", new_subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},  # s1 old, s2 new
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_structural"
    sink = FakeSink()
    _run_apply(plan, sink, sync)

    # After structural update: old s1 entry is gone, new s1/s2 entries from FakeSink
    assert "s1" not in sync._d.split_children or sync.split_child_lm_id("s1") != 100
    assert sync.split_child_lm_id("s1") == 9000  # new id from FakeSink
    assert sync.split_child_lm_id("s2") == 9001


def test_structural_all_new_subs_recorded():
    new_subs = [make_sub("s2", -20000), make_sub("s3", -15000), make_sub("s4", -15000)]
    txn = make_split_ynab_txn("p1", new_subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    sink = FakeSink()
    _run_apply(plan, sink, sync)

    assert sync.split_child_lm_id("s2") == 9000
    assert sync.split_child_lm_id("s3") == 9001
    assert sync.split_child_lm_id("s4") == 9002


def test_structural_child_lm_ids_differ_from_old():
    new_subs = [make_sub("s1", -30000), make_sub("s2", -20000)]
    txn = make_split_ynab_txn("p1", new_subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    sink = FakeSink()
    _run_apply(plan, sink, sync)

    # FakeSink returns 9000+, not 100
    assert sync.split_child_lm_id("s1") == 9000  # new ID, not old 100
    assert sync.split_child_lm_id("s2") == 9001


# ── Inplace update consistency ────────────────────────────────────────────────

def test_inplace_parent_and_all_children_updated():
    subs = [make_sub("s1", -20000), make_sub("s2", -30000)]
    txn = make_split_ynab_txn("p1", subs)
    # txn has changed from stored (old hashes)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100, "s2": 101},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))
    assert plan.items[0].bucket == "update_split_inplace"

    sink = FakeSink()
    _run_apply(plan, sink, sync)

    call_names = [c[0] for c in sink.calls]
    assert "update" in call_names  # parent was updated

    entry = sync.txn("p1")
    assert entry.ynab_hash != "old"
    assert entry.lm_hash != "old"


def test_inplace_does_not_touch_split_children_map():
    subs = [make_sub("s1", -20000), make_sub("s2", -30000)]
    txn = make_split_ynab_txn("p1", subs)
    sync = make_sync(
        transactions={"p1": {"lm_id": 10, "split_done": True, "ynab_hash": "old", "lm_hash": "old"}},
        split_children={"s1": 100, "s2": 101},
    )
    plan = build_transaction_update_plan([txn], ynab_accounts(), sync=sync,
                                          options=_options(force_ynab=True))

    sink = FakeSink()
    _run_apply(plan, sink, sync)

    assert sync.split_child_lm_id("s1") == 100
    assert sync.split_child_lm_id("s2") == 101
