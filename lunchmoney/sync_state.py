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
"""
from __future__ import annotations

import json
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
    # Keys: "payment_transfer", "tracking_off_budget"
    special_categories: dict[str, int] = Field(default_factory=dict)
    # YNAB UUIDs of internal system categories (keyed by role name).
    # Used by Phase 1 to distinguish intentionally-unmapped categories from mapping errors.
    # Keys: "inflow", "uncategorized"
    ynab_internal_cats: dict[str, str] = Field(default_factory=dict)


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
