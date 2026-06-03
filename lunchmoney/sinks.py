"""Transaction sink abstraction: write InsertTransactionObjects to the LM API or a directory."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lm_api_types_generated import InsertTransactionObject, SplitTransactionObject
from lm_client import LMClient
from transactions import SplitRequest


@dataclass
class InsertResult:
    inserted: int
    skipped: int
    skipped_reasons: dict[str, int]  # Reason.value -> count


class TransactionSink(Protocol):
    def existing_ynab_ids(self, *, manual_account_id: int | None,
                          plaid_account_id: int | None) -> set[str]: ...
    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult: ...
    def unsplit_parents(self, split_requests: list[SplitRequest],
                        incomplete_split_cat_id: int) -> dict[str, int]:
        """Return {ynab_parent_id: lm_id} for parents not yet split."""
        ...
    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> None: ...
    def close(self) -> None: ...


class ApiSink:
    """Writes to the Lunch Money API."""

    def __init__(self, client: LMClient) -> None:
        self._client = client

    def existing_ynab_ids(self, *, manual_account_id: int | None,
                          plaid_account_id: int | None) -> set[str]:
        if manual_account_id is not None:
            return set()
        if plaid_account_id is None:
            return set()
        txns = self._client.get_transactions(plaid_account_id=plaid_account_id,
                                             start_date="1900-01-01",
                                             end_date="2100-01-01")
        return {
            str(t.custom_metadata.get("ynab_id", ""))
            for t in txns
            if t.custom_metadata and t.custom_metadata.get("ynab_id")
        }

    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult:
        if not txns:
            return InsertResult(inserted=0, skipped=0, skipped_reasons={})
        resp = self._client.insert_transactions(txns)
        reasons: dict[str, int] = {}
        for skip in resp.skipped_duplicates:
            key = skip.reason.value if skip.reason else "unknown"
            reasons[key] = reasons.get(key, 0) + 1
        return InsertResult(
            inserted=len(resp.transactions),
            skipped=len(resp.skipped_duplicates),
            skipped_reasons=reasons,
        )

    def unsplit_parents(self, split_requests: list[SplitRequest],
                        incomplete_split_cat_id: int) -> dict[str, int]:
        """Query LM for transactions still in the Incomplete Split category."""
        txns = self._client.get_transactions(
            category_id=incomplete_split_cat_id,
            start_date="2000-01-01",
            end_date="2100-01-01",
        )
        result: dict[str, int] = {}
        for t in txns:
            ynab_id = t.custom_metadata.get("ynab_id") if t.custom_metadata else None
            if ynab_id and t.id is not None:
                result[ynab_id] = t.id
        return result

    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> None:
        self._client.split_transaction(parent_lm_id, children)

    def close(self) -> None:
        pass


class DirSink:
    """Writes LM-format JSON to a directory. No network access."""

    def __init__(self, out_dir: Path) -> None:
        self._dir = out_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._txns: list[dict[str, Any]] = []
        self._splits: list[dict[str, Any]] = []
        self._next_id = 1
        self._id_by_external: dict[str, int] = {}  # external_id -> synthetic lm_id

    def existing_ynab_ids(self, *, manual_account_id: int | None,
                          plaid_account_id: int | None) -> set[str]:
        txns_path = self._dir / "transactions.json"
        if not txns_path.exists():
            return set()
        data: list[dict[str, Any]] = json.loads(txns_path.read_text())
        return {
            str(t.get("custom_metadata", {}).get("ynab_id", ""))
            for t in data
            if t.get("custom_metadata", {}).get("ynab_id")
        }

    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult:
        for t in txns:
            d = t.model_dump(mode="json", exclude_none=True)
            synthetic_id = self._next_id
            self._next_id += 1
            if ext := d.get("external_id"):
                self._id_by_external[ext] = synthetic_id
            self._txns.append(d)
        return InsertResult(inserted=len(txns), skipped=0, skipped_reasons={})

    def unsplit_parents(self, split_requests: list[SplitRequest],
                        incomplete_split_cat_id: int) -> dict[str, int]:
        """Return synthetic ids for all split parents (all are 'unsplit' in DirSink)."""
        return {
            sr.ynab_parent_id: self._id_by_external[sr.ynab_parent_id]
            for sr in split_requests
            if sr.ynab_parent_id in self._id_by_external
        }

    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> None:
        self._splits.append({
            "parent_lm_id": parent_lm_id,
            "child_transactions": [c.model_dump(mode="json", exclude_none=True) for c in children],
        })

    def close(self) -> None:
        txns_sorted = sorted(self._txns, key=lambda t: t.get("external_id", ""))
        splits_sorted = sorted(self._splits, key=lambda s: s["parent_lm_id"])
        (self._dir / "transactions.json").write_text(json.dumps(txns_sorted, indent=2))
        (self._dir / "split_pass.json").write_text(json.dumps(splits_sorted, indent=2))
