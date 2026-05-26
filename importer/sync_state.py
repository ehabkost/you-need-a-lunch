"""Local sync state: records YNAB↔LM ID mappings built during import runs.

File: data/<slug>/sync_state.json
Machine-generated — do not edit manually.

Schema:
  schema_version: 1
  ynab_budget_id: str
  ynab_budget_name: str
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
from typing import Optional

SYNC_STATE_FILE = "sync_state.json"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncState:
    def __init__(self, data: dict):
        self._d = data

    @classmethod
    def load_or_create(cls, data_dir: Path, ynab_budget_id: str,
                        ynab_budget_name: str, currency: str) -> "SyncState":
        path = data_dir / SYNC_STATE_FILE
        if path.exists():
            data = json.loads(path.read_text())
        else:
            data = {
                "schema_version": SCHEMA_VERSION,
                "ynab_budget_id": ynab_budget_id,
                "ynab_budget_name": ynab_budget_name,
                "currency": currency,
                "accounts": {},
                "category_groups": {},
                "categories": {},
            }
        return cls(data)

    def save(self, data_dir: Path):
        self._d["last_updated"] = _now()
        (data_dir / SYNC_STATE_FILE).write_text(
            json.dumps(self._d, indent=2, ensure_ascii=False)
        )

    # ── accounts ──────────────────────────────────────────────────────────────

    def account(self, ynab_id: str) -> Optional[dict]:
        return self._d["accounts"].get(ynab_id)

    def set_account(self, ynab_id: str, *, lm_type: str,
                    lm_id: Optional[int], lm_name: str):
        self._d["accounts"][ynab_id] = {
            "lm_type": lm_type,
            "lm_id": lm_id,
            "lm_name": lm_name,
            "synced_at": _now(),
        }

    # ── category groups ───────────────────────────────────────────────────────

    def category_group(self, ynab_id: str) -> Optional[dict]:
        return self._d["category_groups"].get(ynab_id)

    def set_category_group(self, ynab_id: str, *, lm_id: int, lm_name: str):
        self._d["category_groups"][ynab_id] = {
            "lm_id": lm_id,
            "lm_name": lm_name,
            "synced_at": _now(),
        }

    # ── categories ────────────────────────────────────────────────────────────

    def category(self, ynab_id: str) -> Optional[dict]:
        return self._d["categories"].get(ynab_id)

    def set_category(self, ynab_id: str, *, lm_id: int, lm_name: str,
                     lm_group_id: Optional[int] = None):
        self._d["categories"][ynab_id] = {
            "lm_id": lm_id,
            "lm_name": lm_name,
            "lm_group_id": lm_group_id,
            "synced_at": _now(),
        }

    # ── lookups ───────────────────────────────────────────────────────────────

    def lm_account_id(self, ynab_id: str) -> Optional[int]:
        e = self.account(ynab_id)
        return e["lm_id"] if e else None

    def lm_category_id(self, ynab_id: str) -> Optional[int]:
        e = self.category(ynab_id)
        return e["lm_id"] if e else None

    def lm_category_group_id(self, ynab_id: str) -> Optional[int]:
        e = self.category_group(ynab_id)
        return e["lm_id"] if e else None
