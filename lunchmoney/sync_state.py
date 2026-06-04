"""Local sync state: records YNAB↔LM ID mappings built during import runs.

File: data/<slug>/<lm_account_id>/sync_state.json
Machine-generated — do not edit manually.

Schema:
  schema_version: 1
  ynab_budget_id: str
  ynab_budget_name: str
  lm_account_id: int      # Lunch Money account ID (keyed by LM user)
  currency: str           # lowercase ISO 4217
  last_updated: str       # ISO 8601
  accounts:
    <ynab-uuid>:
      lm_type: "manual" | "plaid" | "skipped"
      lm_id: int | null   # null only for lm_type=skipped
      lm_name: str
      synced_at: str
  category_groups:
    <ynab-uuid>:
      lm_id: int
      lm_name: str
      synced_at: str
  categories:
    <ynab-uuid>:
      lm_id: int
      lm_name: str
      lm_group_id: int | null
      synced_at: str
  transactions:
    <ynab-uuid>:
      lm_id: int           # LM transaction ID this YNAB txn was imported as
      split_done: bool     # split parent: True once pass-2 split applied
      synced_at: str
  txn_index_built: bool    # True once the LM-side txn index has been built/reconciled
"""
from __future__ import annotations

import hashlib
import json
import json as _json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

SYNC_STATE_FILE = "sync_state.json"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    lm_type: str                       # manual | plaid | skipped
    lm_id: Optional[int] = None        # null only for lm_type=skipped
    lm_name: str = ""
    synced_at: str = ""


class CategoryGroupEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    lm_id: int
    lm_name: str = ""
    synced_at: str = ""


class CategoryEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    lm_id: int
    lm_name: str = ""
    lm_group_id: Optional[int] = None
    synced_at: str = ""


class TxnEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    lm_id: int
    split_done: bool = False           # split parents only: True once pass-2 split applied
    ynab_hash: str = ""                # hash of YNAB input fields at last import
    lm_hash: str = ""                  # hash of LM payload fields at last import
    synced_at: str = ""


class SyncStateData(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int = SCHEMA_VERSION
    ynab_budget_id: str = ""
    ynab_budget_name: str = ""
    lm_account_id: int = 0
    currency: str = ""
    last_updated: str = ""
    accounts: dict[str, AccountEntry] = Field(default_factory=dict)
    category_groups: dict[str, CategoryGroupEntry] = Field(default_factory=dict)
    categories: dict[str, CategoryEntry] = Field(default_factory=dict)
    # LM-native categories with no YNAB equivalent (keyed by role name)
    # Keys: "payment_transfer", "tracking_off_budget", "incomplete_split"
    special_categories: dict[str, int] = Field(default_factory=dict)
    # YNAB UUIDs of internal system categories (keyed by role name).
    # Used by Phase 1 to distinguish intentionally-unmapped categories from mapping errors.
    # Keys: "inflow", "uncategorized"
    ynab_internal_cats: dict[str, str] = Field(default_factory=dict)
    # Set to True after pass 1 (transaction insert) completes successfully.
    txn_pass1_done: bool = False
    # YNAB txn UUID -> imported LM transaction. Built during import and rebuildable
    # from LM via custom_metadata.ynab_id. Used to skip already-imported txns so we
    # don't re-POST thousands of transactions on every run.
    transactions: dict[str, TxnEntry] = Field(default_factory=dict)
    # True once the LM-side transaction index has been built/reconciled at least once.
    txn_index_built: bool = False
    # YNAB sub.id -> LM child transaction id. Keyed by sub ID (not parent).
    # Populated during Pass 2 split. Used for per-child updates.
    split_children: dict[str, int] = Field(default_factory=dict)
    # checkpoint["transactions"] value at last successful --apply run.
    ynab_txn_server_knowledge: int = 0


class SyncState:
    def __init__(self, data: SyncStateData):
        self._d = data

    @classmethod
    def load_or_create(cls, data_dir: Path, lm_account_id: int, ynab_budget_id: str,
                        ynab_budget_name: str, currency: str) -> Tuple["SyncState", Path]:
        sync_dir = data_dir / str(lm_account_id)
        sync_dir.mkdir(parents=True, exist_ok=True)
        path = sync_dir / SYNC_STATE_FILE

        if path.exists():
            data = SyncStateData(**json.loads(path.read_text()))
        else:
            data = SyncStateData(
                ynab_budget_id=ynab_budget_id,
                ynab_budget_name=ynab_budget_name,
                lm_account_id=lm_account_id,
                currency=currency,
            )
        return cls(data), sync_dir

    def save(self, sync_dir: Path) -> None:
        self._d.last_updated = _now()
        (sync_dir / SYNC_STATE_FILE).write_text(
            self._d.model_dump_json(indent=2)
        )

    # ── accounts ──────────────────────────────────────────────────────────────

    def account(self, ynab_id: str) -> Optional[AccountEntry]:
        return self._d.accounts.get(ynab_id)

    def set_account(self, ynab_id: str, *, lm_type: str,
                    lm_id: Optional[int], lm_name: str) -> None:
        self._d.accounts[ynab_id] = AccountEntry(
            lm_type=lm_type, lm_id=lm_id, lm_name=lm_name, synced_at=_now(),
        )

    # ── category groups ───────────────────────────────────────────────────────

    def category_group(self, ynab_id: str) -> Optional[CategoryGroupEntry]:
        return self._d.category_groups.get(ynab_id)

    def set_category_group(self, ynab_id: str, *, lm_id: int, lm_name: str) -> None:
        self._d.category_groups[ynab_id] = CategoryGroupEntry(
            lm_id=lm_id, lm_name=lm_name, synced_at=_now(),
        )

    # ── categories ────────────────────────────────────────────────────────────

    def category(self, ynab_id: str) -> Optional[CategoryEntry]:
        return self._d.categories.get(ynab_id)

    def set_category(self, ynab_id: str, *, lm_id: int, lm_name: str,
                     lm_group_id: Optional[int] = None) -> None:
        self._d.categories[ynab_id] = CategoryEntry(
            lm_id=lm_id, lm_name=lm_name, lm_group_id=lm_group_id, synced_at=_now(),
        )

    # ── special LM-native categories ─────────────────────────────────────────

    def special_cat_id(self, key: str) -> Optional[int]:
        return self._d.special_categories.get(key)

    def set_special_cat(self, key: str, lm_id: int) -> None:
        self._d.special_categories[key] = lm_id

    # ── YNAB internal system category UUIDs ──────────────────────────────────

    def ynab_internal_cat(self, key: str) -> Optional[str]:
        return self._d.ynab_internal_cats.get(key)

    def set_ynab_internal_cat(self, key: str, ynab_id: str) -> None:
        self._d.ynab_internal_cats[key] = ynab_id

    # ── transactions ──────────────────────────────────────────────────────────

    def txn(self, ynab_id: str) -> Optional[TxnEntry]:
        return self._d.transactions.get(ynab_id)

    def set_txn(self, ynab_id: str, *, lm_id: int, split_done: bool = False,
                ynab_hash: str = "", lm_hash: str = "") -> None:
        self._d.transactions[ynab_id] = TxnEntry(
            lm_id=lm_id, split_done=split_done,
            ynab_hash=ynab_hash, lm_hash=lm_hash, synced_at=_now(),
        )

    def mark_split_done(self, ynab_id: str,
                        child_map: Optional[dict[str, int]] = None) -> None:
        e = self._d.transactions.get(ynab_id)
        if e:
            e.split_done = True
            e.synced_at = _now()
        if child_map:
            self._d.split_children.update(child_map)

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

    def synced_txn_ids(self) -> set[str]:
        return set(self._d.transactions.keys())

    def clear_transactions(self) -> None:
        """Drop the local txn index (used by --rebuild-index before re-scanning LM)."""
        self._d.transactions.clear()
        self._d.txn_index_built = False

    @property
    def txn_index_built(self) -> bool:
        return self._d.txn_index_built

    def set_txn_index_built(self, value: bool = True) -> None:
        self._d.txn_index_built = value

    @property
    def currency(self) -> str:
        return self._d.currency

    # ── lookups ───────────────────────────────────────────────────────────────

    def lm_account_id(self, ynab_id: str) -> Optional[int]:
        e = self.account(ynab_id)
        return e.lm_id if e else None

    def lm_category_id(self, ynab_id: str) -> Optional[int]:
        e = self.category(ynab_id)
        return e.lm_id if e else None

    def lm_category_group_id(self, ynab_id: str) -> Optional[int]:
        e = self.category_group(ynab_id)
        return e.lm_id if e else None


# ── Hash functions (module-level, no I/O) ─────────────────────────────────────

def compute_ynab_hash(txn: dict) -> str:
    """Hash the YNAB fields that drive the LM payload."""
    key: dict = {
        "date":        txn["date"],
        "amount":      txn["amount"],
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
        "date":        fields.get("date"),
        "amount":      fields.get("amount"),
        "payee":       fields.get("payee"),
        "category_id": fields.get("category_id"),
        "notes":       fields.get("notes"),
        "status":      fields.get("status"),
    }
    children = fields.get("split_children")
    if children:
        key["split_children"] = sorted(
            [{"amount": c.get("amount"), "category_id": c.get("category_id"),
              "notes": c.get("notes"), "payee": c.get("payee")}
             for c in children],
            key=lambda c: _json.dumps(c, sort_keys=True),
        )
    return hashlib.sha256(_json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
