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
    lm_hash: str = ""
    child_map: dict[str, int] = field(default_factory=dict)  # sub_ynab_id -> child_lm_id


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
    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> list[int]: ...
    def unsplit(self, parent_lm_id: int) -> None: ...
    def update(self, lm_id: int, payload: dict[str, Any]) -> None: ...
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
        from sync_state import compute_lm_hash
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
            result.append(ScannedTxn(ynab_id=str(ynab_id), lm_id=t.id,
                                     split_done=bool(t.is_split_parent),
                                     lm_hash=lm_hash))
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

    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> list[int]:
        result = self._client.split_transaction(parent_lm_id, children)
        return [c.id for c in (result.children or [])]

    def unsplit(self, parent_lm_id: int) -> None:
        self._client.unsplit_transaction(parent_lm_id)

    def update(self, lm_id: int, payload: dict[str, Any]) -> None:
        self._client.update_transaction(lm_id, payload)

    def close(self) -> None:
        pass


class DirSink:
    """Writes LM-format JSON to a directory. No network access."""

    def __init__(self, out_dir: Path) -> None:
        self._dir = out_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._txns: list[dict[str, Any]] = []
        self._splits: list[dict[str, Any]] = []
        self._unsplits: list[dict[str, Any]] = []
        self._updates: list[dict[str, Any]] = []
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

    def split(self, parent_lm_id: int, children: list[SplitTransactionObject]) -> list[int]:
        child_ids = [self._next_id + i for i in range(len(children))]
        self._next_id += len(children)
        self._splits.append({
            "parent_lm_id": parent_lm_id,
            "child_transactions": [c.model_dump(mode="json", exclude_none=True) for c in children],
        })
        return child_ids

    def unsplit(self, parent_lm_id: int) -> None:
        self._unsplits.append({"parent_lm_id": parent_lm_id})

    def update(self, lm_id: int, payload: dict[str, Any]) -> None:
        self._updates.append({"lm_id": lm_id, "payload": payload})

    def close(self) -> None:
        txns_sorted = sorted(self._txns, key=lambda t: t.get("external_id", ""))
        splits_sorted = sorted(self._splits, key=lambda s: s["parent_lm_id"])
        unsplits_sorted = sorted(self._unsplits, key=lambda s: s["parent_lm_id"])
        updates_sorted = sorted(self._updates, key=lambda u: u["lm_id"])
        (self._dir / "transactions.json").write_text(json.dumps(txns_sorted, indent=2))
        (self._dir / "split_pass.json").write_text(json.dumps(splits_sorted, indent=2))
        (self._dir / "unsplit_pass.json").write_text(json.dumps(unsplits_sorted, indent=2))
        (self._dir / "updates.json").write_text(json.dumps(updates_sorted, indent=2))
