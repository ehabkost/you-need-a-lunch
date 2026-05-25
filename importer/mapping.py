"""Mapping config between YNAB and Lunch Money entities.

The mapping lives in data/<budget-slug>/mapping.yaml and is the single source
of truth for which YNAB entity corresponds to which LM entity.

Schema overview:
  ynab_budget_id: str
  ynab_budget_name: str
  generated_at: str

  accounts:
    <ynab-uuid>:
      lm_type: manual | plaid | excluded
      lm_id: int | null        # null = not yet mapped
      match_method: str | null  # external_id | mask | name | manual

  category_groups:
    <ynab-uuid>: int | null    # LM category ID (is_group=true), null = to create

  categories:
    <ynab-uuid>: int | null    # LM category ID, null = to create

  lm_excluded:
    manual_accounts: [int]     # LM IDs with no YNAB counterpart (suppress audit error)
    plaid_accounts: [int]
    categories: [int]
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

MAPPING_FILE = "mapping.yaml"


class Mapping:
    def __init__(self, data: dict):
        self._d = data

    # ── persistence ───────────────────────────────────────────────────────────

    @classmethod
    def load(cls, data_dir: Path) -> "Mapping":
        path = data_dir / MAPPING_FILE
        if not path.exists():
            raise FileNotFoundError(
                f"No mapping.yaml at {path}. Run 'init-mapping' first."
            )
        with open(path) as f:
            return cls(yaml.safe_load(f))

    def save(self, data_dir: Path):
        path = data_dir / MAPPING_FILE
        with open(path, "w") as f:
            yaml.dump(self._d, f, default_flow_style=False, allow_unicode=True,
                      sort_keys=False)

    # ── metadata ──────────────────────────────────────────────────────────────

    @property
    def ynab_budget_id(self) -> str:
        return self._d["ynab_budget_id"]

    @property
    def ynab_budget_name(self) -> str:
        return self._d.get("ynab_budget_name", "")

    # ── YNAB → LM lookups ────────────────────────────────────────────────────

    def lm_account(self, ynab_id: str) -> Optional[dict]:
        """Return {lm_type, lm_id, match_method} or None."""
        return self._d.get("accounts", {}).get(ynab_id)

    def lm_category_group(self, ynab_id: str) -> Optional[int]:
        return self._d.get("category_groups", {}).get(ynab_id)

    def lm_category(self, ynab_id: str) -> Optional[int]:
        return self._d.get("categories", {}).get(ynab_id)

    # ── LM → YNAB reverse lookups ────────────────────────────────────────────

    def ynab_for_lm_manual(self, lm_id: int) -> Optional[str]:
        for yid, info in self._d.get("accounts", {}).items():
            if isinstance(info, dict) and info.get("lm_type") == "manual" and info.get("lm_id") == lm_id:
                return yid
        return None

    def ynab_for_lm_plaid(self, lm_id: int) -> Optional[str]:
        for yid, info in self._d.get("accounts", {}).items():
            if isinstance(info, dict) and info.get("lm_type") == "plaid" and info.get("lm_id") == lm_id:
                return yid
        return None

    def ynab_for_lm_category(self, lm_id: int) -> Optional[str]:
        for yid, lid in self._d.get("categories", {}).items():
            if lid == lm_id:
                return yid
        return None

    def ynab_for_lm_category_group(self, lm_id: int) -> Optional[str]:
        for yid, lid in self._d.get("category_groups", {}).items():
            if lid == lm_id:
                return yid
        return None

    # ── exclusion checks ─────────────────────────────────────────────────────

    def is_excluded_manual(self, lm_id: int) -> bool:
        return lm_id in self._d.get("lm_excluded", {}).get("manual_accounts", [])

    def is_excluded_plaid(self, lm_id: int) -> bool:
        return lm_id in self._d.get("lm_excluded", {}).get("plaid_accounts", [])

    def is_excluded_category(self, lm_id: int) -> bool:
        return lm_id in self._d.get("lm_excluded", {}).get("categories", [])

    # ── validation ────────────────────────────────────────────────────────────

    def unmapped_ynab_accounts(self) -> list[str]:
        """Return YNAB account IDs where lm_id is null and lm_type != excluded."""
        result = []
        for yid, info in self._d.get("accounts", {}).items():
            if not isinstance(info, dict):
                continue
            if info.get("lm_type") != "excluded" and info.get("lm_id") is None:
                result.append(yid)
        return result

    def unmapped_ynab_categories(self) -> list[str]:
        """Return YNAB category IDs mapped to null (will need creation in LM)."""
        return [yid for yid, lid in self._d.get("categories", {}).items() if lid is None]

    def unmapped_ynab_category_groups(self) -> list[str]:
        return [yid for yid, lid in self._d.get("category_groups", {}).items() if lid is None]

    # ── raw access for init-mapping ───────────────────────────────────────────

    @property
    def raw(self) -> dict:
        return self._d
