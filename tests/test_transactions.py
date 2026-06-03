"""Unit tests for the pure transaction classification core."""
import pytest
from datetime import date

from transactions import (
    TxnImportOptions, build_transaction_plan, preflight_check,
    _CAT_INFLOW, _CAT_UNCATEGORIZED, _CAT_DEFERRED,
)
from conftest import make_sync, manual_account, make_txn

# ── Shared sync state helpers ─────────────────────────────────────────────────

PAYMENT_CAT_ID = 500
TRACKING_CAT_ID = 501
INCOMPLETE_SPLIT_CAT_ID = 502
INFLOW_LM_CAT_ID = 200
GROCERIES_LM_CAT_ID = 300

YNAB_ACCT_ID = "ynab-acct-101"
YNAB_ACCT_ID2 = "ynab-acct-102"
YNAB_CAT_ID_INFLOW = "ynab-cat-inflow"
YNAB_CAT_ID_UNCATEGORIZED = "ynab-cat-uncat"
YNAB_CAT_ID_GROCERIES = "ynab-cat-groceries"


def _base_sync(*, off_budget: bool = False) -> tuple:
    """Returns (sync, ynab_accounts) with a single on/off-budget manual account."""
    sync = make_sync(
        accounts={
            YNAB_ACCT_ID: {"lm_type": "manual", "lm_id": 101, "lm_name": "Account 1"},
        },
        categories={
            YNAB_CAT_ID_INFLOW: {"lm_id": INFLOW_LM_CAT_ID, "lm_name": "Inflow: Ready to Assign"},
            YNAB_CAT_ID_GROCERIES: {"lm_id": GROCERIES_LM_CAT_ID, "lm_name": "Groceries"},
        },
        special_cats={
            "payment_transfer": PAYMENT_CAT_ID,
            "tracking_off_budget": TRACKING_CAT_ID,
            "incomplete_split": INCOMPLETE_SPLIT_CAT_ID,
        },
        ynab_internal_cats={"uncategorized": YNAB_CAT_ID_UNCATEGORIZED},
    )
    ynab_accounts = [
        {"id": YNAB_ACCT_ID, "name": "Account 1", "on_budget": not off_budget,
         "deleted": False, "type": "checking"},
    ]
    return sync, ynab_accounts


def _plan_one(txn: dict, *, off_budget: bool = False,
              options: TxnImportOptions | None = None) -> "ClassifiedTxn":  # noqa: F821
    sync, accts = _base_sync(off_budget=off_budget)
    plan = build_transaction_plan([txn], accts, sync=sync, options=options or TxnImportOptions())
    assert len(plan.items) == 1
    return plan.items[0]


# ── Case 3: Starting Balance + zero → skipped_zero ───────────────────────────

def test_starting_balance_zero_skipped():
    txn = make_txn(amount=0, payee_name="Starting Balance",
                   category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW)
    result = _plan_one(txn)
    assert result.bucket == "skipped_zero"
    assert result.insert is None


# ── Case 4: Starting Balance + non-zero → opening_balance ────────────────────

def test_starting_balance_nonzero_opening_balance():
    txn = make_txn(amount=1000000, payee_name="Starting Balance",
                   category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW)
    result = _plan_one(txn)
    assert result.bucket == "opening_balance"
    assert result.insert is not None
    assert result.insert.amount.root == "-1000.0000"
    assert result.insert.payee == "Starting Balance"
    assert result.insert.category_id is None
    assert result.insert.custom_metadata["ynab_starting_balance"] is True
    assert result.insert.external_id == txn["id"]


def test_starting_balance_opening_balance_category_override():
    txn = make_txn(amount=500000, payee_name="Starting Balance",
                   category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW)
    options = TxnImportOptions(opening_balance_category=999)
    result = _plan_one(txn, options=options)
    assert result.bucket == "opening_balance"
    assert result.insert.category_id == 999


def test_opening_balance_ignores_since():
    """Opening balances are imported even when before --since."""
    txn = make_txn(amount=1000000, payee_name="Starting Balance",
                   category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW,
                   date="2020-01-01")
    options = TxnImportOptions(since=date(2024, 1, 1))
    result = _plan_one(txn, options=options)
    assert result.bucket == "opening_balance"


# ── Case 1 & 5: Transfers ─────────────────────────────────────────────────────

def test_transfer_paired_both_migrated():
    sync = make_sync(
        accounts={
            YNAB_ACCT_ID: {"lm_type": "manual", "lm_id": 101, "lm_name": "A1"},
            YNAB_ACCT_ID2: {"lm_type": "manual", "lm_id": 102, "lm_name": "A2"},
        },
        special_cats={"payment_transfer": PAYMENT_CAT_ID,
                      "tracking_off_budget": TRACKING_CAT_ID,
                      "incomplete_split": INCOMPLETE_SPLIT_CAT_ID},
    )
    txn = make_txn(
        amount=-100000,
        transfer_account_id=YNAB_ACCT_ID2,
        transfer_transaction_id="paired-txn-id",
        category_id=YNAB_CAT_ID_UNCATEGORIZED,
        category_name=_CAT_UNCATEGORIZED,
    )
    accts = [
        {"id": YNAB_ACCT_ID, "name": "A1", "on_budget": True, "deleted": False, "type": "checking"},
        {"id": YNAB_ACCT_ID2, "name": "A2", "on_budget": True, "deleted": False, "type": "checking"},
    ]
    plan = build_transaction_plan([txn], accts, sync=sync, options=TxnImportOptions())
    result = plan.items[0]
    assert result.bucket == "transfer_paired"
    assert result.insert.category_id == PAYMENT_CAT_ID
    assert result.insert.custom_metadata["ynab_paired_id"] == "paired-txn-id"


def test_transfer_one_sided_other_not_migrated():
    sync = make_sync(
        accounts={
            YNAB_ACCT_ID: {"lm_type": "manual", "lm_id": 101, "lm_name": "A1"},
        },
        special_cats={"payment_transfer": PAYMENT_CAT_ID,
                      "tracking_off_budget": TRACKING_CAT_ID,
                      "incomplete_split": INCOMPLETE_SPLIT_CAT_ID},
    )
    txn = make_txn(
        amount=-100000,
        transfer_account_id="ynab-acct-not-migrated",
        transfer_transaction_id="other-txn-id",
        category_id=YNAB_CAT_ID_UNCATEGORIZED,
        category_name=_CAT_UNCATEGORIZED,
    )
    accts = [
        {"id": YNAB_ACCT_ID, "name": "A1", "on_budget": True, "deleted": False, "type": "checking"},
    ]
    plan = build_transaction_plan([txn], accts, sync=sync, options=TxnImportOptions())
    result = plan.items[0]
    assert result.bucket == "transfer_one_sided"
    assert result.insert.category_id == PAYMENT_CAT_ID


# ── Case 2: Inflow: Ready to Assign, non-transfer ─────────────────────────────

def test_inflow_income():
    txn = make_txn(amount=3000000, category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW,
                   payee_name="Employer")
    result = _plan_one(txn)
    assert result.bucket == "income"
    assert result.insert.category_id == INFLOW_LM_CAT_ID
    assert result.insert.amount.root == "-3000.0000"  # inflow in YNAB → negative in LM (credit)


# ── Case 6: Uncategorized regular spending ────────────────────────────────────

def test_uncategorized_regular():
    txn = make_txn(amount=-25000, category_id=YNAB_CAT_ID_UNCATEGORIZED,
                   category_name=_CAT_UNCATEGORIZED)
    result = _plan_one(txn)
    assert result.bucket == "uncategorized"
    assert result.insert.category_id is None
    assert result.insert.custom_metadata["ynab_uncategorized"] is True


# ── Case 9: Deferred Income SubCategory ───────────────────────────────────────

def test_deferred_income_needs_decision_by_default():
    txn = make_txn(amount=500000, category_id="ynab-deferred",
                   category_name=_CAT_DEFERRED)
    result = _plan_one(txn)
    assert result.bucket == "needs_decision"
    assert result.insert is None


def test_deferred_income_as_income():
    sync, accts = _base_sync()
    sync._d.categories["ynab-deferred"] = __import__("sync_state").CategoryEntry(
        lm_id=999, lm_name="Deferred Income"
    )
    txn = make_txn(amount=500000, category_id="ynab-deferred",
                   category_name=_CAT_DEFERRED)
    plan = build_transaction_plan([txn], accts, sync=sync,
                                  options=TxnImportOptions(deferred_income_as="income"))
    assert plan.items[0].bucket == "income"


def test_deferred_income_as_uncategorized():
    txn = make_txn(amount=500000, category_id="ynab-deferred",
                   category_name=_CAT_DEFERRED)
    result = _plan_one(txn, options=TxnImportOptions(deferred_income_as="uncategorized"))
    assert result.bucket == "uncategorized"
    assert result.insert.custom_metadata["ynab_uncategorized"] is True


def test_deferred_income_as_skip():
    txn = make_txn(amount=500000, category_id="ynab-deferred",
                   category_name=_CAT_DEFERRED)
    result = _plan_one(txn, options=TxnImportOptions(deferred_income_as="skip"))
    assert result.bucket == "skipped_before_since"
    assert result.insert is None


# ── Off-budget (tracking) ─────────────────────────────────────────────────────

def test_off_budget_spending_becomes_tracking():
    txn = make_txn(amount=-40000, category_id=YNAB_CAT_ID_GROCERIES,
                   category_name="Groceries")
    result = _plan_one(txn, off_budget=True)
    assert result.bucket == "tracking"
    assert result.insert.category_id == TRACKING_CAT_ID


# ── Balance adjustments ───────────────────────────────────────────────────────

def test_balance_adjustment_payee():
    txn = make_txn(amount=0, payee_name="Manual Balance Adjustment",
                   category_id=YNAB_CAT_ID_UNCATEGORIZED, category_name=_CAT_UNCATEGORIZED)
    result = _plan_one(txn)
    assert result.bucket == "balance_adjustment"
    assert result.insert.category_id is None
    assert result.insert.payee == "Manual Balance Adjustment"


def test_reconciliation_balance_adjustment():
    txn = make_txn(amount=100, payee_name="Reconciliation Balance Adjustment",
                   category_id=YNAB_CAT_ID_UNCATEGORIZED, category_name=_CAT_UNCATEGORIZED)
    result = _plan_one(txn)
    assert result.bucket == "balance_adjustment"


# ── Deleted ───────────────────────────────────────────────────────────────────

def test_deleted_skipped():
    txn = make_txn(deleted=True)
    result = _plan_one(txn)
    assert result.bucket == "skipped_deleted"
    assert result.insert is None


# ── Normal spending ───────────────────────────────────────────────────────────

def test_normal_spending_mapped_category():
    txn = make_txn(amount=-50000, category_id=YNAB_CAT_ID_GROCERIES,
                   category_name="Groceries", payee_name="Supermarket")
    result = _plan_one(txn)
    assert result.bucket == "spending"
    assert result.insert.category_id == GROCERIES_LM_CAT_ID
    assert result.insert.amount.root == "50.0000"
    assert result.insert.external_id == txn["id"]
    assert result.insert.manual_account_id == 101
    assert result.insert.plaid_account_id is None


def test_normal_spending_reviewed_status():
    txn = make_txn(amount=-10000, approved=True,
                   category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries")
    result = _plan_one(txn)
    assert result.insert.status == "reviewed"


def test_normal_spending_unreviewed_status():
    txn = make_txn(amount=-10000, approved=False,
                   category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries")
    result = _plan_one(txn)
    assert result.insert.status == "unreviewed"


# ── --since filter ────────────────────────────────────────────────────────────

def test_since_filters_old_transactions():
    txn = make_txn(amount=-10000, date="2022-06-01",
                   category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries")
    result = _plan_one(txn, options=TxnImportOptions(since=date(2024, 1, 1)))
    assert result.bucket == "skipped_before_since"
    assert result.insert is None


def test_since_passes_transactions_on_cutoff():
    txn = make_txn(amount=-10000, date="2024-01-01",
                   category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries")
    result = _plan_one(txn, options=TxnImportOptions(since=date(2024, 1, 1)))
    assert result.bucket == "spending"


# ── Amount sign conversion ────────────────────────────────────────────────────

def test_amount_sign_negation():
    """YNAB outflow (negative) → LM debit (positive); YNAB inflow (positive) → LM credit (negative)."""
    expense = make_txn(amount=-53350, category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries")
    income = make_txn(amount=2000000, category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW)
    expense_result = _plan_one(expense)
    income_result = _plan_one(income)
    assert expense_result.insert.amount.root == "53.3500"
    assert income_result.insert.amount.root == "-2000.0000"


# ── Splits ────────────────────────────────────────────────────────────────────

def test_split_parent_gets_incomplete_split_category():
    txn = make_txn(
        amount=-53350,
        category_id="ynab-cat-split", category_name="Split",
        subtransactions=[
            {"id": "sub-1", "amount": -33350, "category_id": YNAB_CAT_ID_GROCERIES,
             "category_name": "Groceries", "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": False},
            {"id": "sub-2", "amount": -20000, "category_id": YNAB_CAT_ID_GROCERIES,
             "category_name": "Groceries", "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": False},
        ],
    )
    result = _plan_one(txn)
    assert result.bucket == "splits_native"
    assert result.insert is not None
    assert result.insert.category_id == INCOMPLETE_SPLIT_CAT_ID
    assert result.insert.custom_metadata["ynab_is_split_parent"] is True
    assert result.insert.amount.root == "53.3500"
    assert result.insert.external_id == txn["id"]
    assert result.split_children is not None
    assert len(result.split_children) == 2


def test_split_children_amounts_sum_to_parent():
    txn = make_txn(
        amount=-53350,
        category_id="ynab-cat-split", category_name="Split",
        subtransactions=[
            {"id": "sub-1", "amount": -33350, "category_id": YNAB_CAT_ID_GROCERIES,
             "category_name": "Groceries", "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": False},
            {"id": "sub-2", "amount": -20000, "category_id": YNAB_CAT_ID_GROCERIES,
             "category_name": "Groceries", "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": False},
        ],
    )
    result = _plan_one(txn)
    parent_amount = float(result.insert.amount.root)
    child_total = sum(float(c.amount.root) for c in result.split_children)
    assert abs(parent_amount - child_total) < 0.0001


def test_split_with_transfer_sub():
    txn = make_txn(
        amount=-53350,
        category_id="ynab-cat-split", category_name="Split",
        subtransactions=[
            {"id": "sub-1", "amount": -33350, "category_id": YNAB_CAT_ID_GROCERIES,
             "category_name": "Groceries", "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": False},
            {"id": "sub-2", "amount": -20000, "category_id": None,
             "category_name": "Transfer", "memo": None, "payee_name": None,
             "transfer_account_id": YNAB_ACCT_ID2, "deleted": False},
        ],
    )
    result = _plan_one(txn)
    assert result.split_children[0].category_id == GROCERIES_LM_CAT_ID
    assert result.split_children[1].category_id == PAYMENT_CAT_ID


def test_split_deleted_sub_excluded():
    txn = make_txn(
        amount=-33350,
        category_id="ynab-cat-split", category_name="Split",
        subtransactions=[
            {"id": "sub-1", "amount": -33350, "category_id": YNAB_CAT_ID_GROCERIES,
             "category_name": "Groceries", "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": False},
            {"id": "sub-2", "amount": 0, "category_id": None,
             "category_name": None, "memo": None, "payee_name": None,
             "transfer_account_id": None, "deleted": True},
        ],
    )
    result = _plan_one(txn)
    assert len(result.split_children) == 1


# ── counts ────────────────────────────────────────────────────────────────────

def test_counts_correct():
    sync, accts = _base_sync()
    txns = [
        make_txn(id="t1", amount=-10000, category_id=YNAB_CAT_ID_GROCERIES,
                 category_name="Groceries"),
        make_txn(id="t2", amount=0, payee_name="Starting Balance",
                 category_id=YNAB_CAT_ID_INFLOW, category_name=_CAT_INFLOW),
        make_txn(id="t3", deleted=True,
                 category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries"),
    ]
    plan = build_transaction_plan(txns, accts, sync=sync, options=TxnImportOptions())
    assert plan.counts["spending"] == 1
    assert plan.counts["skipped_zero"] == 1
    assert plan.counts["skipped_deleted"] == 1


# ── preflight_check ───────────────────────────────────────────────────────────

def test_preflight_passes_when_all_good():
    sync, accts = _base_sync()
    txns = [make_txn(category_id=YNAB_CAT_ID_GROCERIES, category_name="Groceries")]
    errors = preflight_check(txns, accts, sync)
    assert errors == []


def test_preflight_fails_missing_payment_transfer():
    sync = make_sync(
        accounts={YNAB_ACCT_ID: {"lm_type": "manual", "lm_id": 101, "lm_name": "A1"}},
        special_cats={},  # no payment_transfer!
    )
    accts = [{"id": YNAB_ACCT_ID, "name": "A1", "on_budget": True, "deleted": False}]
    txns = [make_txn()]
    errors = preflight_check(txns, accts, sync)
    assert any("payment_transfer" in e or "Payment, Transfer" in e for e in errors)
