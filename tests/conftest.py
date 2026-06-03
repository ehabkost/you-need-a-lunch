"""Shared fixtures for transaction importer tests."""
import sys
from pathlib import Path

# Make lunchmoney/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "lunchmoney"))

import pytest
from sync_state import (
    SyncState, SyncStateData, AccountEntry, CategoryEntry, CategoryGroupEntry
)


def make_sync(
    *,
    currency: str = "cad",
    accounts: dict | None = None,
    categories: dict | None = None,
    special_cats: dict | None = None,
    ynab_internal_cats: dict | None = None,
) -> SyncState:
    """Build a SyncState in memory with the given entities."""
    data = SyncStateData(
        ynab_budget_id="budget-1",
        ynab_budget_name="Test Budget",
        lm_account_id=99999,
        currency=currency,
        accounts={k: AccountEntry(**v) for k, v in (accounts or {}).items()},
        categories={k: CategoryEntry(**v) for k, v in (categories or {}).items()},
        special_categories=special_cats or {},
        ynab_internal_cats=ynab_internal_cats or {},
    )
    return SyncState(data)


def manual_account(lm_id: int, on_budget: bool = True) -> tuple[dict, dict]:
    """Returns (ynab_account_dict, sync_entry_dict) for a manual account."""
    ynab_id = f"ynab-acct-{lm_id}"
    return (
        {"id": ynab_id, "name": f"Account {lm_id}", "on_budget": on_budget,
         "deleted": False, "type": "checking"},
        {"lm_type": "manual", "lm_id": lm_id, "lm_name": f"Account {lm_id}"},
    )


def make_txn(
    *,
    id: str = "txn-1",
    date: str = "2024-01-15",
    amount: int = -50000,  # milliunits
    account_id: str = "ynab-acct-101",
    category_id: str | None = "cat-groceries",
    category_name: str | None = "Groceries",
    payee_name: str | None = "Supermarket",
    memo: str | None = None,
    approved: bool = True,
    deleted: bool = False,
    transfer_account_id: str | None = None,
    transfer_transaction_id: str | None = None,
    subtransactions: list | None = None,
    flag_color: str | None = None,
) -> dict:
    return {
        "id": id,
        "date": date,
        "amount": amount,
        "account_id": account_id,
        "category_id": category_id,
        "category_name": category_name,
        "payee_name": payee_name,
        "memo": memo,
        "approved": approved,
        "deleted": deleted,
        "transfer_account_id": transfer_account_id,
        "transfer_transaction_id": transfer_transaction_id,
        "subtransactions": subtransactions or [],
        "flag_color": flag_color,
    }
