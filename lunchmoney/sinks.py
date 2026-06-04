"""Transaction sink abstraction: write InsertTransactionObjects to the LM API or a directory."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lm_api_types_generated import InsertTransactionObject, SplitTransactionObject
from lm_client import LMClient


@dataclass
class ScannedTxn:
    """One already-imported transaction discovered on the LM side."""
    ynab_id: str
    lm_id: int
    split_done: bool = False


@dataclass
class InsertResult:
    inserted: int
    skipped: int
    skipped_reasons: dict[str, int]            # Reason.value -> count
    id_by_external: dict[str, int] = field(default_factory=dict)  # ynab_id -> LM txn id


class TransactionSink(Protocol):
    def scan_imported(self) -> list[ScannedTxn]:
        """Return all transactions already imported by this tool (ynab_id ↔ lm_id pairs)."""
        ...
    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult: ...
    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> None: ...
    def close(self) -> None: ...


class ApiSink:
    """Writes to the Lunch Money API."""

    def __init__(self, client: LMClient) -> None:
        self._client = client

    def scan_imported(self) -> list[ScannedTxn]:
        """Rebuild the ynab_id↔lm_id index from LM via custom_metadata.ynab_id.

        include_split_parents pulls already-split parents into the same paginated
        scan (they're flagged is_split_parent), so no per-parent lookups are needed.
        Split children carry no ynab_id and are ignored.
        """
        txns = self._client.get_transactions(
            start_date="1900-01-01", end_date="2100-01-01",
            include_split_parents="true",
        )
        result: list[ScannedTxn] = []
        for t in txns:
            ynab_id = (t.custom_metadata or {}).get("ynab_id")
            if not ynab_id:
                continue
            # A split parent that has already been split is flagged is_split_parent;
            # an unsplit parent still looks like a normal txn (split_done stays False).
            result.append(ScannedTxn(ynab_id=str(ynab_id), lm_id=t.id,
                                     split_done=bool(t.is_split_parent)))
        return result

    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult:
        if not txns:
            return InsertResult(inserted=0, skipped=0, skipped_reasons={})
        resp = self._client.insert_transactions(txns)
        reasons: dict[str, int] = {}
        id_by_external: dict[str, int] = {}
        for t in resp.transactions:
            if t.external_id and t.id is not None:
                id_by_external[t.external_id] = t.id
        for skip in resp.skipped_duplicates:
            key = skip.reason.value if skip.reason else "unknown"
            reasons[key] = reasons.get(key, 0) + 1
            # Self-heal: record the existing pair so we won't resend it next time.
            ext = skip.request_transaction.external_id if skip.request_transaction else None
            if ext and skip.existing_transaction_id is not None:
                id_by_external[ext] = skip.existing_transaction_id
        return InsertResult(
            inserted=len(resp.transactions),
            skipped=len(resp.skipped_duplicates),
            skipped_reasons=reasons,
            id_by_external=id_by_external,
        )

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

    def scan_imported(self) -> list[ScannedTxn]:
        """No persistent LM side in --to-dir mode; nothing pre-exists to reconcile."""
        return []

    def insert(self, txns: list[InsertTransactionObject]) -> InsertResult:
        id_by_external: dict[str, int] = {}
        for t in txns:
            d = t.model_dump(mode="json", exclude_none=True)
            synthetic_id = self._next_id
            self._next_id += 1
            if ext := d.get("external_id"):
                id_by_external[ext] = synthetic_id
            self._txns.append(d)
        return InsertResult(inserted=len(txns), skipped=0, skipped_reasons={},
                            id_by_external=id_by_external)

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
