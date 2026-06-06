"""Tests for lunchmoney/export.py — the full LM-data snapshot writer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lunchmoney"))

from pydantic import BaseModel

import export
from export import LMExport, write_export, export_to_dir


# Lightweight stand-ins — export only relies on .model_dump(), not the full
# generated schemas (which carry many required fields irrelevant here).
class _Obj(BaseModel):
    id: int
    name: str = ""


class _Txn(BaseModel):
    id: int
    custom_metadata: dict | None = None


class _FakeClient:
    """Stand-in for LMClient returning canned objects."""
    def __init__(self, manual, plaid, cats, txns):
        self._manual, self._plaid, self._cats, self._txns = manual, plaid, cats, txns
        self.txn_calls: list[dict] = []

    def get_manual_accounts(self):
        return self._manual

    def get_plaid_accounts(self):
        return self._plaid

    def get_categories(self):
        return self._cats

    def get_transactions(self, **kwargs):
        self.txn_calls.append(kwargs)
        return self._txns


def _sample_export() -> LMExport:
    return LMExport(
        manual_accounts=[_Obj(id=101, name="Checking")],
        plaid_accounts=[],
        categories=[_Obj(id=300, name="Groceries")],
        transactions=[_Txn(id=1, custom_metadata={"ynab_id": "abc"})],
    )


def test_write_export_creates_all_files(tmp_path: Path) -> None:
    write_export(_sample_export(), tmp_path)
    for fname in (export.MANUAL_ACCOUNTS_FILE, export.PLAID_ACCOUNTS_FILE,
                  export.CATEGORIES_FILE, export.TRANSACTIONS_FILE):
        assert (tmp_path / fname).exists(), fname

    manual = json.loads((tmp_path / export.MANUAL_ACCOUNTS_FILE).read_text())
    assert manual[0]["id"] == 101
    plaid = json.loads((tmp_path / export.PLAID_ACCOUNTS_FILE).read_text())
    assert plaid == []
    txns = json.loads((tmp_path / export.TRANSACTIONS_FILE).read_text())
    assert txns[0]["custom_metadata"]["ynab_id"] == "abc"


def test_write_export_creates_missing_dir(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "lm_account"
    write_export(_sample_export(), out)
    assert (out / export.CATEGORIES_FILE).exists()


def test_export_to_dir_fetches_once_and_returns(tmp_path: Path) -> None:
    sample = _sample_export()
    client = _FakeClient(sample.manual_accounts, sample.plaid_accounts,
                         sample.categories, sample.transactions)
    result = export_to_dir(client, tmp_path)  # type: ignore[arg-type]

    assert len(client.txn_calls) == 1  # transactions fetched exactly once
    assert result.transactions[0].id == 1
    assert (tmp_path / export.TRANSACTIONS_FILE).exists()
