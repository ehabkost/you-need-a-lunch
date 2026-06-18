"""Tests for ynab/export.py merge logic.

Loaded via importlib under a unique module name because lunchmoney/export.py is also
named ``export`` and conftest puts lunchmoney/ on sys.path first — a plain ``import
export`` here would collide in sys.modules.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ynab_export", Path(__file__).parent.parent / "ynab" / "export.py"
)
ynab_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ynab_export)


def _cat(cid: str, gid: str, name: str = "", deleted: bool = False) -> dict:
    return {"id": cid, "category_group_id": gid, "name": name or cid, "deleted": deleted}


def _cat_ids(groups: list) -> set[str]:
    return {c["id"] for g in groups for c in g.get("categories", [])}


def test_merge_categories_preserves_unchanged_siblings() -> None:
    """The core bug: YNAB's delta returns a changed group carrying ONLY its changed
    categories. A group-level replace drops the unchanged siblings; merge_categories
    must keep them."""
    existing = [
        {"id": "G1", "name": "Grp1", "categories": [
            _cat("A", "G1"), _cat("B", "G1"), _cat("C", "G1")]},
        {"id": "G2", "name": "Grp2", "categories": [_cat("D", "G2")]},
    ]
    # Delta: G1 returned with only the changed category A (B and C absent).
    delta = [{"id": "G1", "name": "Grp1", "categories": [_cat("A", "G1", "A-new")]}]

    merged, updated, added = ynab_export.merge_categories(existing, delta)

    assert _cat_ids(merged) == {"A", "B", "C", "D"}
    assert (updated, added) == (1, 0)
    a = next(c for g in merged for c in g["categories"] if c["id"] == "A")
    assert a["name"] == "A-new"


def test_merge_categories_handles_move_between_groups() -> None:
    """A category moved to another group is returned nested under the new group; it must
    not be left duplicated under the old one."""
    existing = [
        {"id": "G1", "name": "Grp1", "categories": [_cat("A", "G1")]},
        {"id": "G2", "name": "Grp2", "categories": [_cat("D", "G2")]},
    ]
    delta = [{"id": "G1", "name": "Grp1", "categories": [_cat("D", "G1", "D-moved")]}]

    merged, _, _ = ynab_export.merge_categories(existing, delta)
    by_group = {g["id"]: [c["id"] for c in g["categories"]] for g in merged}

    assert sorted(by_group["G1"]) == ["A", "D"]
    assert by_group["G2"] == []  # D no longer here, not duplicated


def test_merge_categories_adds_new_group_and_category() -> None:
    existing = [{"id": "G1", "name": "Grp1", "categories": [_cat("A", "G1")]}]
    delta = [{"id": "G2", "name": "Grp2", "categories": [_cat("E", "G2")]}]

    merged, updated, added = ynab_export.merge_categories(existing, delta)

    assert _cat_ids(merged) == {"A", "E"}
    assert (updated, added) == (0, 1)


def test_merge_categories_keeps_deleted_tombstones() -> None:
    existing = [{"id": "G1", "name": "Grp1", "categories": [_cat("A", "G1")]}]
    delta = [{"id": "G1", "name": "Grp1", "categories": [_cat("A", "G1", deleted=True)]}]

    merged, _, _ = ynab_export.merge_categories(existing, delta)
    a = next(c for g in merged for c in g["categories"] if c["id"] == "A")
    assert a["deleted"] is True


def test_resolve_since_forces_full_when_file_missing(tmp_path: Path) -> None:
    """Deleting <name>.json must force a full re-fetch even when a cursor is stored, so a
    single resource can be rebuilt without re-fetching everything."""
    checkpoint = {"categories": 5603}
    # File absent -> None (full fetch)
    assert ynab_export._resolve_since(tmp_path, "categories", checkpoint, "categories") is None
    # File present -> stored cursor (delta)
    (tmp_path / "categories.json").write_text("[]")
    assert ynab_export._resolve_since(tmp_path, "categories", checkpoint, "categories") == 5603
    # No cursor at all -> None
    assert ynab_export._resolve_since(tmp_path, "categories", {}, "categories") is None
