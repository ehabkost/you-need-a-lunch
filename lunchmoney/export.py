"""Export all Lunch Money data to a local directory.

Writes a faithful snapshot of the LM side (manual accounts, plaid accounts,
categories, and every transaction) as JSON files. Used by `import --rebuild-index`
to save the LM data it already fetches while rebuilding the local txn index, so
the snapshot lands beside the sync state in data/<slug>/<lm_account_id>/.

Files written (one JSON array each):
  manual_accounts.json
  plaid_accounts.json
  categories.json
  transactions.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel

from lm_api_types_generated import (
    CategoryObject,
    ManualAccountObject,
    PlaidAccountObject,
    TransactionObject,
)
from lm_client import LMClient

MANUAL_ACCOUNTS_FILE = "manual_accounts.json"
PLAID_ACCOUNTS_FILE = "plaid_accounts.json"
CATEGORIES_FILE = "categories.json"
TRANSACTIONS_FILE = "transactions.json"


@dataclass
class LMExport:
    """A snapshot of the Lunch Money side."""
    manual_accounts: list[ManualAccountObject]
    plaid_accounts: list[PlaidAccountObject]
    categories: list[CategoryObject]
    transactions: list[TransactionObject]


def fetch_all(client: LMClient) -> LMExport:
    """Fetch all LM data. Read-only. Transactions include split parents + children."""
    return LMExport(
        manual_accounts=client.get_manual_accounts(),
        plaid_accounts=client.get_plaid_accounts(),
        categories=client.get_categories(),
        transactions=client.get_transactions(
            start_date="1900-01-01", end_date="2100-01-01",
            include_split_parents="true",
            include_children="true",
        ),
    )


def _write_list(path: Path, models: Sequence[BaseModel]) -> None:
    path.write_text(
        json.dumps([m.model_dump(mode="json") for m in models], indent=2)
    )


def write_export(export: LMExport, out_dir: Path) -> None:
    """Write the snapshot as JSON arrays into *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_list(out_dir / MANUAL_ACCOUNTS_FILE, export.manual_accounts)
    _write_list(out_dir / PLAID_ACCOUNTS_FILE, export.plaid_accounts)
    _write_list(out_dir / CATEGORIES_FILE, export.categories)
    _write_list(out_dir / TRANSACTIONS_FILE, export.transactions)


def export_to_dir(client: LMClient, out_dir: Path) -> LMExport:
    """Fetch all LM data, write it to *out_dir*, and return it."""
    export = fetch_all(client)
    write_export(export, out_dir)
    return export
