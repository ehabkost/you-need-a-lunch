#!/usr/bin/env python3
"""Lunch Money importer — import YNAB data into Lunch Money.

Usage:
  ./run.sh ./importer/import.py --data data/cad init-mapping   # generate mapping.yaml
  ./run.sh ./importer/import.py --data data/cad show-mapping   # display mapping table (no API)
  ./run.sh ./importer/import.py --data data/cad audit          # strict YNAB-first audit
  ./run.sh ./importer/import.py --data data/cad import         # dry-run (not yet implemented)
  ./run.sh ./importer/import.py --data data/cad import --apply # apply    (not yet implemented)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import re

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from lm_client import LMClient
from mapping import MAPPING_FILE, Mapping

LM_CACHE_FILE = "lm_cache.json"

# ── YNAB account type → LM account type ──────────────────────────────────────

YNAB_TO_LM_TYPE = {
    "checking":      "other asset",
    "savings":       "other asset",
    "cash":          "cash",
    "creditCard":    "credit",
    "otherAsset":    "other asset",
    "otherLiability":"other liability",
}

# ── helpers ───────────────────────────────────────────────────────────────────

def get_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Error: {name} is not set", file=sys.stderr)
        sys.exit(1)
    return v


def load_json(data_dir: Path, name: str) -> list | dict:
    return json.loads((data_dir / f"{name}.json").read_text())


def load_lm_cache(data_dir: Path) -> dict:
    path = data_dir / LM_CACHE_FILE
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_lm_cache(data_dir: Path, manual: list, plaid: list, cats_flat: list):
    (data_dir / LM_CACHE_FILE).write_text(json.dumps({
        "manual_accounts": manual,
        "plaid_accounts":  plaid,
        "categories":      cats_flat,
    }, indent=2))


def patch_yaml_account(text: str, uuid: str, lm_type: str, lm_id, match_method) -> str:
    """Update lm_type/lm_id/match_method for one account entry, preserving comments."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if line.rstrip() == f'  "{uuid}":':
            out.append(line)
            i += 1
            while i < len(lines) and lines[i].startswith("    "):
                l = lines[i]
                if l.startswith("    lm_type:"):
                    out.append(f"    lm_type: {lm_type}")
                elif l.startswith("    lm_id:"):
                    out.append(f"    lm_id: {'null' if lm_id is None else lm_id}")
                elif l.startswith("    match_method:"):
                    out.append(f"    match_method: {'null' if not match_method else match_method}")
                else:
                    out.append(l)
                i += 1
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def patch_yaml_scalar(text: str, uuid: str, lm_id) -> str:
    """Update a scalar category/group mapping line: '  "uuid": value'."""
    val = "null" if lm_id is None else str(lm_id)
    return re.sub(
        rf'^(  "{re.escape(uuid)}":\s*)\S+',
        rf"\g<1>{val}",
        text,
        flags=re.MULTILINE,
    )


def remove_from_lm_excluded(text: str, lm_id: int) -> str:
    """Remove a specific LM ID line from the lm_excluded section."""
    return re.sub(rf"^  - {lm_id}[ \t]*.*$\n?", "", text, flags=re.MULTILINE)


def append_to_lm_excluded(text: str, key: str, lm_id: int) -> str:
    """Append lm_id to lm_excluded.<key>, handling both '[]' and multi-line forms."""
    # Handle inline empty list:  key: []  →  key:\n  - id
    text = re.sub(
        rf"^(  {key}): \[\]$",
        rf"\1:\n  - {lm_id}",
        text, flags=re.MULTILINE,
    )
    # Handle multi-line list: insert after "  key:" line
    text = re.sub(
        rf"^(  {key}:)$",
        rf"\1\n  - {lm_id}",
        text, flags=re.MULTILINE,
    )
    return text


def normalize(s: str) -> str:
    """Lowercase, strip emoji and punctuation for fuzzy name matching."""
    import unicodedata
    s = s.lower().strip()
    # remove emoji / non-ASCII for comparison
    s = "".join(c for c in s if unicodedata.category(c) not in ("So", "Sk"))
    return " ".join(s.split())


def name_similarity(a: str, b: str) -> float:
    """Very simple overlap score, 0–1."""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return 1.0
    words_a = set(na.split())
    words_b = set(nb.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))


# ── init-mapping ──────────────────────────────────────────────────────────────

def cmd_init_mapping(data_dir: Path, client: LMClient):
    """Generate an initial mapping.yaml from YNAB export + current LM state."""

    # load YNAB export
    ynab_accounts: list[dict] = load_json(data_dir, "accounts")
    ynab_groups_raw: list[dict] = load_json(data_dir, "categories")  # category groups with embedded categories
    meta: dict = load_json(data_dir, "export_metadata")

    # flatten YNAB categories
    ynab_groups = [g for g in ynab_groups_raw if not g["deleted"] and not g["internal"]]
    ynab_cats_by_group: dict[str, list[dict]] = {}
    for g in ynab_groups:
        ynab_cats_by_group[g["id"]] = [c for c in g.get("categories", []) if not c["deleted"] and not c["internal"]]

    # fetch LM state
    print("Fetching Lunch Money accounts...")
    lm_manual = client.get_manual_accounts()
    lm_plaid = client.get_plaid_accounts()
    lm_cats_raw = client.get_categories()

    lm_cats_flat_for_cache: list[dict] = []
    for _c in lm_cats_raw:
        lm_cats_flat_for_cache.append(_c)
        lm_cats_flat_for_cache.extend(_c.get("children", []))
    save_lm_cache(data_dir, lm_manual, lm_plaid, lm_cats_flat_for_cache)

    # index LM manual accounts by external_id
    lm_manual_by_ext: dict[str, dict] = {}
    for a in lm_manual:
        ext = a.get("external_id")
        if ext:
            lm_manual_by_ext[ext] = a

    # index LM manual accounts by normalised name
    lm_manual_by_name: dict[str, dict] = {normalize(a["name"]): a for a in lm_manual}

    # index LM plaid accounts by normalised institution+type for matching
    # (YNAB doesn't expose account numbers, so we match on institution + account type)
    def plaid_key(a: dict) -> str:
        return normalize(f"{a.get('institution_name', '')} {a.get('type', '')} {a.get('subtype', '')}")
    lm_plaid_by_key: dict[str, dict] = {plaid_key(a): a for a in lm_plaid}

    # index LM categories
    lm_cats_flat: list[dict] = []
    for c in lm_cats_raw:
        lm_cats_flat.append(c)
        lm_cats_flat.extend(c.get("children", []))

    lm_groups_by_name: dict[str, dict] = {
        normalize(c["name"]): c for c in lm_cats_flat if c.get("is_group")
    }
    lm_cats_by_name: dict[str, dict] = {
        normalize(c["name"]): c for c in lm_cats_flat if not c.get("is_group")
    }
    # also index child cats by (group_id, name)
    lm_cats_by_group_and_name: dict[tuple, dict] = {}
    for c in lm_cats_flat:
        if not c.get("is_group") and c.get("group_id") is not None:
            lm_cats_by_group_and_name[(c["group_id"], normalize(c["name"]))] = c

    # ── build mapping data ────────────────────────────────────────────────────

    accounts_map: dict = {}
    for a in sorted(ynab_accounts, key=lambda x: (not x["on_budget"], x["name"])):
        if a["deleted"]:
            continue
        yid = a["id"]
        entry: dict = {"lm_type": None, "lm_id": None, "match_method": None}

        # 1. external_id match (previously imported manual account)
        if yid in lm_manual_by_ext:
            lm_a = lm_manual_by_ext[yid]
            entry = {"lm_type": "manual", "lm_id": lm_a["id"], "match_method": "external_id"}

        # 2. direct_import_linked → try to match to plaid account
        elif a.get("direct_import_linked"):
            ynab_type = a["type"]
            lm_type_equiv = {"creditCard": "credit"}.get(ynab_type, "depository")
            note = (a.get("note") or "").strip()

            # 2a. mask found in YNAB account note → definitive match
            note_match = None
            for lm_pa in lm_plaid:
                mask = lm_pa.get("mask", "")
                if mask and mask in note:
                    note_match = lm_pa
                    break

            if note_match:
                entry = {
                    "lm_type": "plaid",
                    "lm_id": note_match["id"],
                    "match_method": "note_mask",
                    "_lm_mask": note_match.get("mask"),
                    "_lm_name": note_match.get("display_name") or note_match.get("name"),
                }

            else:
                # 2b. fall back to name similarity — only auto-fill if very confident
                best_score, best_match = 0.0, None
                for lm_pa in lm_plaid:
                    inst_score = name_similarity(
                        a.get("name", ""),
                        lm_pa.get("display_name") or lm_pa.get("name", "")
                    )
                    type_match = 1.0 if lm_pa.get("type", "") == lm_type_equiv else 0.0
                    score = inst_score * 0.7 + type_match * 0.3
                    if score > best_score:
                        best_score, best_match = score, lm_pa
                if best_match and best_score >= 0.9:
                    entry = {
                        "lm_type": "plaid",
                        "lm_id": best_match["id"],
                        "match_method": "name",
                        "_match_score": round(best_score, 2),
                        "_lm_mask": best_match.get("mask"),
                        "_lm_name": best_match.get("display_name") or best_match.get("name"),
                    }
                elif best_match and best_score >= 0.5:
                    entry = {
                        "lm_type": "plaid",
                        "lm_id": None,
                        "match_method": None,
                        "_match_score": round(best_score, 2),
                        "_lm_mask": best_match.get("mask"),
                        "_lm_name": best_match.get("display_name") or best_match.get("name"),
                    }
                else:
                    entry = {
                        "lm_type": "plaid",
                        "lm_id": None,
                        "match_method": None,
                        "_unmatched_plaid_accounts": [
                            f"id={p['id']} {p.get('institution_name','')} {p.get('name','')} mask={p.get('mask','?')} ({p.get('type','')})"
                            for p in lm_plaid
                        ],
                    }

        # 3. name match for non-import-linked manual accounts
        else:
            norm = normalize(a["name"])
            if norm in lm_manual_by_name:
                lm_a = lm_manual_by_name[norm]
                entry = {"lm_type": "manual", "lm_id": lm_a["id"], "match_method": "name"}
            else:
                entry = {"lm_type": "manual", "lm_id": None, "match_method": None}

        accounts_map[yid] = entry

    # category groups
    groups_map: dict = {}
    for g in ynab_groups:
        norm = normalize(g["name"])
        if norm in lm_groups_by_name:
            lm_g = lm_groups_by_name[norm]
            groups_map[g["id"]] = lm_g["id"]
        else:
            groups_map[g["id"]] = None

    # categories
    cats_map: dict = {}
    for g in ynab_groups:
        lm_group_id = groups_map.get(g["id"])
        for c in ynab_cats_by_group.get(g["id"], []):
            norm = normalize(c["name"])
            # prefer match within the same group
            if lm_group_id and (lm_group_id, norm) in lm_cats_by_group_and_name:
                cats_map[c["id"]] = lm_cats_by_group_and_name[(lm_group_id, norm)]["id"]
            elif norm in lm_cats_by_name:
                cats_map[c["id"]] = lm_cats_by_name[norm]["id"]
            else:
                cats_map[c["id"]] = None

    # ── write mapping.yaml ────────────────────────────────────────────────────

    out_path = data_dir / MAPPING_FILE

    # build annotated YAML manually so we can embed helpful comments
    lines = [
        f"# YNAB → Lunch Money mapping — {meta['budget_name']} ({meta['currency']})",
        f"# YNAB budget ID: {meta['budget_id']}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        "#",
        "# HOW TO FILL THIS IN:",
        "#   accounts:",
        "#     lm_type: manual | plaid | excluded",
        "#     lm_id:   integer ID from Lunch Money (null = not yet matched)",
        "#   category_groups / categories: LM integer ID, or null to create",
        "#   lm_excluded: LM entity IDs that have no YNAB counterpart",
        "#                (suppresses 'unmapped' audit errors)",
        "",
        f"ynab_budget_id: \"{meta['budget_id']}\"",
        f"ynab_budget_name: \"{meta['budget_name']}\"",
        "",
    ]

    # accounts section
    lines.append("accounts:")
    on_budget  = [a for a in ynab_accounts if not a["deleted"] and     a["on_budget"]]
    off_budget = [a for a in ynab_accounts if not a["deleted"] and not a["on_budget"]]

    def write_account_block(account: dict):
        yid = account["id"]
        entry = accounts_map[yid]
        bal = account.get("balance_formatted", "?")
        flags = []
        if account.get("closed"):        flags.append("closed")
        if account.get("direct_import_linked"): flags.append("direct-import")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(f"  # {account['name']}  [{account['type']}]{flag_str}  balance: {bal}")

        if entry.get("_unmatched_plaid_accounts"):
            lines.append("  # ⚠ No plaid account matched. Available LM plaid accounts:")
            for pa in entry["_unmatched_plaid_accounts"]:
                lines.append(f"  #   {pa}")
            entry_clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        elif entry.get("_lm_name"):
            method = entry.get("match_method")
            if method == "note_mask":
                confidence = "matched by mask in note"
            elif entry.get("lm_id"):
                confidence = "auto-matched by name"
            else:
                confidence = f"suggested by name (score={entry.get('_match_score','?')}) — verify!"
            lines.append(f"  # LM: {entry['_lm_name']}  mask={entry.get('_lm_mask','?')}  ({confidence})")
            entry_clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        else:
            entry_clean = entry

        lines.append(f"  \"{yid}\":")
        for k, v in entry_clean.items():
            if v is None:
                lines.append(f"    {k}: null")
            elif isinstance(v, str):
                lines.append(f"    {k}: {v}")
            else:
                lines.append(f"    {k}: {v}")
        lines.append("")

    lines.append("  # ── On Budget ─────────────────────────────────────────────────────────")
    for a in on_budget:
        write_account_block(a)
    lines.append("  # ── Off Budget ────────────────────────────────────────────────────────")
    for a in off_budget:
        write_account_block(a)

    # category groups section
    lines.append("category_groups:")
    for g in ynab_groups:
        lm_id = groups_map[g["id"]]
        note = "(name match)" if lm_id else "⚠ no match — will be created"
        lines.append(f"  # {g['name']}  →  {note}")
        v = str(lm_id) if lm_id else "null"
        lines.append(f"  \"{g['id']}\": {v}")
    lines.append("")

    # categories section
    lines.append("categories:")
    for g in ynab_groups:
        lines.append(f"  # ── {g['name']} ──")
        for c in ynab_cats_by_group.get(g["id"], []):
            lm_id = cats_map[c["id"]]
            note = "(name match)" if lm_id else "⚠ no match — will be created"
            lines.append(f"  # {c['name']}  →  {note}")
            v = str(lm_id) if lm_id else "null"
            lines.append(f"  \"{c['id']}\": {v}")
    lines.append("")

    lines.append("lm_excluded:")
    lines.append("  manual_accounts: []")
    lines.append("  plaid_accounts: []")
    lines.append("  categories: []")

    out_path.write_text("\n".join(lines) + "\n")

    # count LM entities without a YNAB match
    mapped_manual_ids = {
        info["lm_id"] for info in accounts_map.values()
        if isinstance(info, dict) and info.get("lm_type") == "manual" and info.get("lm_id")
    }
    mapped_plaid_ids = {
        info["lm_id"] for info in accounts_map.values()
        if isinstance(info, dict) and info.get("lm_type") == "plaid" and info.get("lm_id")
    }
    mapped_cat_ids = set(v for v in cats_map.values() if v) | set(v for v in groups_map.values() if v)

    orphan_manual = [a["id"] for a in lm_manual if a["id"] not in mapped_manual_ids]
    orphan_plaid  = [a["id"] for a in lm_plaid  if a["id"] not in mapped_plaid_ids]
    orphan_cats   = [c["id"] for c in lm_cats_flat_for_cache if c["id"] not in mapped_cat_ids]

    auto = sum(1 for v in accounts_map.values() if isinstance(v, dict) and v.get("lm_id"))
    unmatched_acc = sum(1 for v in accounts_map.values() if isinstance(v, dict) and not v.get("lm_id"))
    auto_cat = sum(1 for v in cats_map.values() if v)
    unmatched_cat = sum(1 for v in cats_map.values() if v is None)
    auto_grp = sum(1 for v in groups_map.values() if v)
    unmatched_grp = sum(1 for v in groups_map.values() if v is None)

    print(f"\n  → {out_path}")
    print(f"\n  Accounts:         {auto} matched, {unmatched_acc} need manual mapping")
    print(f"  Category groups:  {auto_grp} matched, {unmatched_grp} to create")
    print(f"  Categories:       {auto_cat} matched, {unmatched_cat} to create")
    if orphan_manual or orphan_plaid or orphan_cats:
        print(f"\n  ⚠ LM has entities not yet mapped to YNAB — run fix-mapping to resolve:")
        if orphan_manual: print(f"    {len(orphan_manual)} manual account(s)")
        if orphan_plaid:  print(f"    {len(orphan_plaid)} plaid account(s)")
        if orphan_cats:   print(f"    {len(orphan_cats)} category/group(s)")
    print()


# ── audit ─────────────────────────────────────────────────────────────────────

def cmd_audit(data_dir: Path, client: LMClient):
    """Strict YNAB-first audit: every LM entity must map to a YNAB entity."""
    mapping = Mapping.load(data_dir)

    print("Fetching Lunch Money state...")
    lm_manual = client.get_manual_accounts()
    lm_plaid  = client.get_plaid_accounts()
    lm_cats_raw = client.get_categories()

    lm_cats_flat: list[dict] = []
    for c in lm_cats_raw:
        lm_cats_flat.append(c)
        lm_cats_flat.extend(c.get("children", []))

    errors: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    # ── manual accounts ───────────────────────────────────────────────────────
    print("Checking manual accounts...")
    for a in lm_manual:
        lid = a["id"]
        name = a["name"]

        if mapping.is_excluded_manual(lid):
            ok.append(f"  [excluded]  manual account {lid} '{name}'")
            continue

        ynab_id = mapping.ynab_for_lm_manual(lid)

        if not ynab_id:
            # check if external_id gives us a clue
            ext = a.get("external_id")
            if ext:
                errors.append(
                    f"  ✗ manual account {lid} '{name}' has external_id={ext} "
                    f"but is not in mapping.yaml — add it to accounts or lm_excluded"
                )
            else:
                errors.append(
                    f"  ✗ manual account {lid} '{name}' has no YNAB mapping "
                    f"— add to mapping.yaml accounts (lm_type: manual, lm_id: {lid}) "
                    f"or to lm_excluded.manual_accounts"
                )
            continue

        # verify external_id on the LM account matches the mapped YNAB account
        ext = a.get("external_id")
        if ext and ext != ynab_id:
            warnings.append(
                f"  ⚠ manual account {lid} '{name}' external_id={ext} "
                f"doesn't match mapped YNAB ID {ynab_id}"
            )
        elif not ext:
            warnings.append(
                f"  ⚠ manual account {lid} '{name}' has no external_id set "
                f"(will be set on first import run)"
            )
        else:
            ok.append(f"  ✓ manual account {lid} '{name}' → YNAB {ynab_id[:8]}…")

    # ── plaid accounts ────────────────────────────────────────────────────────
    print("Checking plaid accounts...")
    for a in lm_plaid:
        lid = a["id"]
        name = a.get("display_name") or a.get("name", "?")
        mask = a.get("mask", "?")

        if mapping.is_excluded_plaid(lid):
            ok.append(f"  [excluded]  plaid account {lid} '{name}' mask={mask}")
            continue

        ynab_id = mapping.ynab_for_lm_plaid(lid)
        if not ynab_id:
            errors.append(
                f"  ✗ plaid account {lid} '{name}' mask={mask} has no YNAB mapping "
                f"— add to mapping.yaml accounts (lm_type: plaid, lm_id: {lid}) "
                f"or to lm_excluded.plaid_accounts"
            )
        else:
            ok.append(f"  ✓ plaid account {lid} '{name}' mask={mask} → YNAB {ynab_id[:8]}…")

    # ── categories ────────────────────────────────────────────────────────────
    print("Checking categories...")
    for c in lm_cats_flat:
        lid = c["id"]
        name = c["name"]
        is_group = c.get("is_group", False)

        if mapping.is_excluded_category(lid):
            ok.append(f"  [excluded]  {'group' if is_group else 'category'} {lid} '{name}'")
            continue

        if is_group:
            ynab_id = mapping.ynab_for_lm_category_group(lid)
        else:
            ynab_id = mapping.ynab_for_lm_category(lid)

        if not ynab_id:
            kind = "category group" if is_group else "category"
            errors.append(
                f"  ✗ {kind} {lid} '{name}' has no YNAB mapping "
                f"— add to mapping.yaml or to lm_excluded.categories"
            )
        else:
            kind = "group" if is_group else "cat"
            ok.append(f"  ✓ {kind} {lid} '{name}' → YNAB {ynab_id[:8]}…")

    # ── transactions spot-check ───────────────────────────────────────────────
    print("Spot-checking recent transactions on manual accounts...")
    mapped_manual_ids = {
        info["lm_id"] for info in mapping.raw.get("accounts", {}).values()
        if isinstance(info, dict) and info.get("lm_type") == "manual" and info.get("lm_id")
    }
    txn_errors = 0
    for lm_id in mapped_manual_ids:
        txns = client.get_transactions(manual_account_id=lm_id, start_date="2020-01-01",
                                       end_date=datetime.now().strftime("%Y-%m-%d"))
        no_ext = [t for t in txns if not t.get("external_id")]
        if no_ext:
            txn_errors += len(no_ext)
            errors.append(
                f"  ✗ manual account {lm_id}: {len(no_ext)} transaction(s) "
                f"missing external_id (not imported via this tool)"
            )
        else:
            ok.append(f"  ✓ manual account {lm_id}: {len(txns)} transaction(s) all have external_id")

    # ── report ────────────────────────────────────────────────────────────────
    print()
    if ok:
        print(f"OK ({len(ok)}):")
        for line in ok:
            print(line)
        print()

    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for line in warnings:
            print(line)
        print()

    if errors:
        print(f"ERRORS ({len(errors)}) — fix these before importing:")
        for line in errors:
            print(line)
        print()
        sys.exit(1)
    else:
        print("✓ Audit passed — all LM entities are mapped to YNAB.")


# ── show-mapping ─────────────────────────────────────────────────────────────

USE_COLOR = sys.stdout.isatty()

def _c(code): return f"\033[{code}m" if USE_COLOR else ""
RESET = _c(0); BOLD = _c(1); DIM = _c(2)
GREEN = _c(32); YELLOW = _c(33); RED = _c(31); CYAN = _c(36)


def _col(text: str, width: int, right=False) -> str:
    import re
    from wcwidth import wcswidth
    stripped = re.sub(r"\033\[[0-9;]*m", "", text)
    pad = max(0, width - (wcswidth(stripped) or len(stripped)))
    return (" " * pad + text) if right else (text + " " * pad)


def cmd_show_mapping(data_dir: Path):
    """Display the current mapping in a readable table (no API calls)."""
    mapping = Mapping.load(data_dir)
    cache = load_lm_cache(data_dir)

    ynab_accounts: list[dict] = load_json(data_dir, "accounts")
    ynab_groups_raw: list[dict] = load_json(data_dir, "categories")
    meta: dict = load_json(data_dir, "export_metadata")

    ynab_groups = [g for g in ynab_groups_raw if not g["deleted"] and not g["internal"]]
    ynab_cats_by_group: dict[str, list[dict]] = {
        g["id"]: [c for c in g.get("categories", []) if not c["deleted"] and not c["internal"]]
        for g in ynab_groups
    }

    raw = mapping.raw
    excl = raw.get("lm_excluded", {})

    cache_manual = {a["id"]: a for a in cache.get("manual_accounts", [])}
    cache_plaid  = {a["id"]: a for a in cache.get("plaid_accounts",  [])}
    cache_cats   = {c["id"]: c for c in cache.get("categories",      [])}

    def lm_acc_name(entry: dict) -> str:
        lm_id  = entry.get("lm_id")
        lm_typ = entry.get("lm_type")
        if lm_id is None:
            return RED + "(unmatched)" + RESET
        if lm_typ == "plaid":
            a = cache_plaid.get(lm_id, {})
            name = a.get("display_name") or a.get("name") or "?"
            mask = f" ···{a['mask']}" if a.get("mask") else ""
            return f"{name}{mask}"
        if lm_typ == "manual":
            a = cache_manual.get(lm_id, {})
            return a.get("name") or "?"
        return "?"

    # ── accounts ──────────────────────────────────────────────────────────────
    print(BOLD + f"\nACCOUNTS — {meta['budget_name']} ({meta['currency']})\n" + RESET)
    print(DIM + "  " + _col("YNAB Account", 34) + _col("LM Account", 34) + "Match" + RESET)
    print(DIM + "  " + "─" * 76 + RESET)

    on_budget  = [a for a in ynab_accounts if not a["deleted"] and     a["on_budget"]]
    off_budget = [a for a in ynab_accounts if not a["deleted"] and not a["on_budget"]]

    matched_acc = needs_acc = 0

    def print_account_group(label: str, accounts: list[dict]):
        nonlocal matched_acc, needs_acc
        if not accounts:
            return
        print(f"\n  {BOLD}{CYAN}{label}{RESET}")
        for a in accounts:
            entry = raw.get("accounts", {}).get(a["id"])
            flags = []
            if a.get("closed"):               flags.append("closed")
            if a.get("direct_import_linked"): flags.append("sync")
            flag_str = f" ({','.join(flags)})" if flags else ""
            ynab_name = a["name"] + flag_str

            if not isinstance(entry, dict):
                status   = RED + "✗" + RESET
                lm_name  = RED + "(not in mapping)" + RESET
                method   = ""
                needs_acc += 1
            elif entry.get("lm_id") is None:
                status   = YELLOW + "⚠" + RESET
                lm_name  = lm_acc_name(entry)
                method   = ""
                needs_acc += 1
            else:
                status   = GREEN + "✓" + RESET
                lm_name  = lm_acc_name(entry)
                method   = DIM + (entry.get("match_method") or "") + RESET
                matched_acc += 1

            print("  " + _col(status + " " + ynab_name, 38)
                  + _col(lm_name, 38) + method)

    print_account_group("On Budget",  on_budget)
    print_account_group("Off Budget", off_budget)

    # unmapped LM accounts (in cache but not referenced in mapping)
    referenced_manual = {
        info["lm_id"] for info in raw.get("accounts", {}).values()
        if isinstance(info, dict) and info.get("lm_type") == "manual" and info.get("lm_id")
    }
    referenced_plaid = {
        info["lm_id"] for info in raw.get("accounts", {}).values()
        if isinstance(info, dict) and info.get("lm_type") == "plaid" and info.get("lm_id")
    }
    excluded_manual = set(excl.get("manual_accounts", []))
    excluded_plaid  = set(excl.get("plaid_accounts",  []))

    unmapped_manual = [a for a in cache.get("manual_accounts", [])
                       if a["id"] not in referenced_manual and a["id"] not in excluded_manual]
    unmapped_plaid  = [a for a in cache.get("plaid_accounts",  [])
                       if a["id"] not in referenced_plaid  and a["id"] not in excluded_plaid]

    if unmapped_manual or unmapped_plaid:
        print(f"\n  {BOLD}{RED}Unmapped LM accounts — run fix-mapping{RESET}")
        for a in unmapped_manual:
            print(f"  {RED}⚠ [manual] {a['id']:8}  {a['name']}{RESET}")
        for a in unmapped_plaid:
            name = a.get("display_name") or a.get("name", "?")
            mask = f"  ···{a['mask']}" if a.get("mask") else ""
            print(f"  {RED}⚠ [plaid]  {a['id']:8}  {name}{mask}{RESET}")

    # ── categories ────────────────────────────────────────────────────────────
    groups_map = raw.get("category_groups", {})
    cats_map   = raw.get("categories", {})

    total_groups = total_cats = matched_groups = matched_cats = 0

    print(BOLD + f"\nCATEGORIES\n" + RESET)
    print(DIM + "  " + _col("YNAB Category", 36) + _col("LM Category", 30) + "Status" + RESET)
    print(DIM + "  " + "─" * 76 + RESET)

    for g in ynab_groups:
        gid   = g["id"]
        lm_gid = groups_map.get(gid)
        total_groups += 1

        if lm_gid is None:
            g_lm_name = RED + "(to create)" + RESET
            g_status  = YELLOW + "to create" + RESET
        else:
            gc = cache_cats.get(lm_gid, {})
            g_lm_name = GREEN + (gc.get("name") or str(lm_gid)) + RESET
            g_status  = GREEN + "matched" + RESET
            matched_groups += 1

        print(f"\n  {BOLD}{CYAN}{_col(g['name'], 36)}{RESET}{_col(g_lm_name, 34)}{g_status}")

        for c in ynab_cats_by_group.get(gid, []):
            cid   = c["id"]
            lm_cid = cats_map.get(cid)
            total_cats += 1
            if lm_cid is None:
                c_lm_name = RED + "(to create)" + RESET
                c_status  = YELLOW + "to create" + RESET
            else:
                cc = cache_cats.get(lm_cid, {})
                c_lm_name = GREEN + (cc.get("name") or str(lm_cid)) + RESET
                c_status  = GREEN + "matched" + RESET
                matched_cats += 1
            print(f"    {_col(c['name'], 34)}{_col(c_lm_name, 34)}{c_status}")

    # unmapped LM categories
    referenced_cats = {v for v in cats_map.values() if v} | {v for v in groups_map.values() if v}
    excluded_cats   = set(excl.get("categories", []))
    unmapped_cats   = [c for c in cache.get("categories", [])
                       if c["id"] not in referenced_cats and c["id"] not in excluded_cats]

    if unmapped_cats:
        print(f"\n  {BOLD}{RED}Unmapped LM categories — run fix-mapping{RESET}")
        for c in unmapped_cats:
            kind = "group" if c.get("is_group") else "cat  "
            print(f"  {RED}⚠ [{kind}] {c['id']:8}  {c.get('name','?')}{RESET}")

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}Summary{RESET}")
    print(f"  Accounts:        {GREEN}{matched_acc} matched{RESET}"
          + (f", {RED}{needs_acc} need mapping{RESET}" if needs_acc else ""))
    unmapped_lm_acc = len(unmapped_manual) + len(unmapped_plaid)
    if unmapped_lm_acc:
        print(f"  Unmapped LM:     {RED}{unmapped_lm_acc} account(s) not yet matched{RESET}")
    print(f"  Category groups: {GREEN}{matched_groups}/{total_groups} matched{RESET}"
          + (f", {YELLOW}{total_groups - matched_groups} to create{RESET}"
             if total_groups > matched_groups else ""))
    print(f"  Categories:      {GREEN}{matched_cats}/{total_cats} matched{RESET}"
          + (f", {YELLOW}{total_cats - matched_cats} to create{RESET}"
             if total_cats > matched_cats else ""))
    if unmapped_cats:
        print(f"  Unmapped LM:     {RED}{len(unmapped_cats)} category/group(s) not yet matched{RESET}")
    print()


# ── fix-mapping ───────────────────────────────────────────────────────────────

def _prompt(prompt: str) -> str:
    """Read one line from stdin; raise KeyboardInterrupt on EOF."""
    try:
        return input(prompt).strip()
    except EOFError:
        raise KeyboardInterrupt


def _lm_acc_desc(a: dict, kind: str) -> str:
    if kind == "plaid":
        name = a.get("display_name") or a.get("name", "?")
        mask = f"  mask={a['mask']}" if "mask" in a else ""
        return f"{name}{mask}  ({a.get('type','?')}/{a.get('subtype','?')})"
    bal = a.get("balance", "?")
    return f"{a['name']}  ({a.get('type','?')}  bal={bal})"


def cmd_fix_mapping(data_dir: Path, client: LMClient):
    """Interactively map unmatched LM entities to YNAB entities."""
    mapping_path = data_dir / MAPPING_FILE

    print("Fetching Lunch Money state...")
    lm_manual = client.get_manual_accounts()
    lm_plaid   = client.get_plaid_accounts()
    lm_cats_raw = client.get_categories()

    lm_cats_flat: list[dict] = []
    for c in lm_cats_raw:
        lm_cats_flat.append(c)
        lm_cats_flat.extend(c.get("children", []))

    save_lm_cache(data_dir, lm_manual, lm_plaid, lm_cats_flat)

    ynab_accounts: list[dict] = load_json(data_dir, "accounts")
    ynab_groups_raw: list[dict] = load_json(data_dir, "categories")

    ynab_acc_by_id = {a["id"]: a for a in ynab_accounts}
    ynab_groups = [g for g in ynab_groups_raw if not g["deleted"] and not g["internal"]]
    ynab_cats_flat: list[dict] = []
    for g in ynab_groups:
        for c in g.get("categories", []):
            if not c["deleted"] and not c["internal"]:
                ynab_cats_flat.append(c)
    ynab_cat_by_id = {c["id"]: c for c in ynab_cats_flat}
    ynab_group_by_id = {g["id"]: g for g in ynab_groups}

    text = mapping_path.read_text()
    raw  = yaml.safe_load(text)

    accounts_section = raw.get("accounts", {})
    groups_section   = raw.get("category_groups", {})
    cats_section     = raw.get("categories", {})
    excl             = raw.get("lm_excluded", {})

    # IDs already referenced in the mapping
    mapped_manual_ids = {
        info["lm_id"] for info in accounts_section.values()
        if isinstance(info, dict) and info.get("lm_type") == "manual" and info.get("lm_id")
    }
    mapped_plaid_ids = {
        info["lm_id"] for info in accounts_section.values()
        if isinstance(info, dict) and info.get("lm_type") == "plaid" and info.get("lm_id")
    }
    excluded_manual_ids = set(excl.get("manual_accounts", []))
    excluded_plaid_ids  = set(excl.get("plaid_accounts",  []))
    excluded_cat_ids    = set(excl.get("categories",      []))
    mapped_cat_ids      = {v for v in cats_section.values() if v} | {v for v in groups_section.values() if v}

    # YNAB candidates: those with lm_id still null (available to be claimed)
    def ynab_acc_candidates(_lm_type: str) -> list[tuple[str, dict]]:
        return [
            (yid, ynab_acc_by_id[yid])
            for yid, info in accounts_section.items()
            if isinstance(info, dict)
            and info.get("lm_id") is None
            and info.get("lm_type") != "excluded"
            and yid in ynab_acc_by_id
        ]

    def ynab_cat_candidates(is_group: bool) -> list[tuple[str, dict]]:
        if is_group:
            return [(yid, ynab_group_by_id[yid]) for yid, v in groups_section.items()
                    if v is None and yid in ynab_group_by_id]
        return [(yid, ynab_cat_by_id[yid]) for yid, v in cats_section.items()
                if v is None and yid in ynab_cat_by_id]

    changed = False

    def save():
        nonlocal text
        mapping_path.write_text(text)
        print(f"  → Saved {mapping_path}")

    def do_account_section(lm_items: list[dict], lm_type: str, section_name: str,
                            covered_ids: set, excluded_ids: set):
        nonlocal text, changed
        unresolved = [
            a for a in lm_items
            if a["id"] not in covered_ids and a["id"] not in excluded_ids
        ] + [
            a for a in lm_items
            if a["id"] in excluded_ids
        ]
        if not unresolved:
            print(f"  (all {section_name} already mapped)")
            return

        # sort: truly missing first, then excluded
        unresolved.sort(key=lambda a: a["id"] not in excluded_ids)

        for idx, lm_acc in enumerate(unresolved):
            lid = lm_acc["id"]
            desc = _lm_acc_desc(lm_acc, lm_type)
            print(f"\n  LM {lm_type} [{lid}]  {BOLD}{desc}{RESET}")

            cands = ynab_acc_candidates(lm_type)
            if cands:
                print(f"  YNAB candidates (currently unmatched):")
                for i, (yid, ya) in enumerate(cands, 1):
                    flags = []
                    if ya.get("closed"): flags.append("closed")
                    if ya.get("direct_import_linked"): flags.append("sync")
                    flag_s = f" ({','.join(flags)})" if flags else ""
                    bal = ya.get("balance_formatted", "")
                    print(f"    {i:2}.  {ya['name']}{flag_s}  [{ya['type']}]  {bal}")
            else:
                print(f"  (no unmatched YNAB {lm_type} accounts — delete this LM account or add a YNAB entry first)")

            print(f"  s=skip for now  q=quit+save")
            while True:
                try:
                    choice = _prompt("  > ")
                except KeyboardInterrupt:
                    save()
                    return

                if choice == "q":
                    save()
                    return
                elif choice == "s":
                    print(f"  → Skipped")
                    break
                elif choice.isdigit():
                    n = int(choice) - 1
                    if cands and 0 <= n < len(cands):
                        ynab_id, ya = cands[n]
                        text = patch_yaml_account(text, ynab_id, lm_type, lid, "manual")
                        if lid in excluded_ids:
                            text = remove_from_lm_excluded(text, lid)
                            excluded_ids.discard(lid)
                        covered_ids.add(lid)
                        changed = True
                        print(f"  → Mapped YNAB '{ya['name']}' → LM {lid}")
                        break
                    else:
                        print(f"  Invalid — enter 1–{len(cands)} or s/q")
                else:
                    print(f"  Invalid — enter a number or s/q")

    def do_category_section(lm_items: list[dict], is_group: bool):
        nonlocal text, changed
        kind = "group" if is_group else "category"
        unresolved = [
            c for c in lm_items
            if c["id"] not in mapped_cat_ids and c["id"] not in excluded_cat_ids
        ] + [
            c for c in lm_items
            if c["id"] in excluded_cat_ids
        ]
        if not unresolved:
            print(f"  (all LM {kind}s already mapped)")
            return

        for lm_cat in unresolved:
            lid = lm_cat["id"]
            name = lm_cat.get("name", "?")
            print(f"\n  LM {kind} [{lid}]  {BOLD}{name}{RESET}")

            cands = ynab_cat_candidates(is_group)
            if cands:
                print(f"  YNAB {kind} candidates (currently unmatched):")
                for i, (yid, yc) in enumerate(cands, 1):
                    print(f"    {i:2}.  {yc['name']}")
            else:
                print(f"  (no unmatched YNAB {kind}s — delete this LM {kind} or add a YNAB entry first)")

            print(f"  s=skip for now  q=quit+save")
            while True:
                try:
                    choice = _prompt("  > ")
                except KeyboardInterrupt:
                    save()
                    return

                if choice == "q":
                    save()
                    return
                elif choice == "s":
                    print(f"  → Skipped")
                    break
                elif choice.isdigit():
                    n = int(choice) - 1
                    if cands and 0 <= n < len(cands):
                        ynab_id, yc = cands[n]
                        if is_group:
                            text = patch_yaml_scalar(text, ynab_id, lid)
                            groups_section[ynab_id] = lid
                        else:
                            text = patch_yaml_scalar(text, ynab_id, lid)
                            cats_section[ynab_id] = lid
                        if lid in excluded_cat_ids:
                            text = remove_from_lm_excluded(text, lid)
                            excluded_cat_ids.discard(lid)
                        mapped_cat_ids.add(lid)
                        changed = True
                        print(f"  → Mapped YNAB '{yc['name']}' → LM {lid}")
                        break
                    else:
                        print(f"  Invalid — enter 1–{len(cands)} or s/q")
                else:
                    print(f"  Invalid — enter a number or s/q")

    try:
        print(f"\n{BOLD}─── Manual Accounts ───{RESET}")
        do_account_section(lm_manual, "manual", "manual accounts",
                           mapped_manual_ids, excluded_manual_ids)

        print(f"\n{BOLD}─── Plaid Accounts ────{RESET}")
        do_account_section(lm_plaid, "plaid", "plaid accounts",
                           mapped_plaid_ids, excluded_plaid_ids)

        lm_groups = [c for c in lm_cats_flat if c.get("is_group")]
        lm_cats   = [c for c in lm_cats_flat if not c.get("is_group")]

        print(f"\n{BOLD}─── Category Groups ───{RESET}")
        do_category_section(lm_groups, is_group=True)

        print(f"\n{BOLD}─── Categories ────────{RESET}")
        do_category_section(lm_cats, is_group=False)

    except KeyboardInterrupt:
        pass

    if changed:
        save()
    else:
        print("\nNo changes made.")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import YNAB data into Lunch Money.")
    parser.add_argument("--data", metavar="DIR", required=True,
                        help="Path to YNAB export directory (e.g. data/brl)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-mapping",  help="Generate initial mapping.yaml from YNAB export + LM state")
    sub.add_parser("show-mapping",  help="Display current mapping as a table (no API calls)")
    sub.add_parser("fix-mapping",   help="Interactively map unmatched LM entities to YNAB entities")
    sub.add_parser("audit",         help="Verify every LM entity maps to a YNAB entity")
    p_import = sub.add_parser("import", help="Import YNAB data into LM (dry-run by default)")
    p_import.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")

    args = parser.parse_args()
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"Error: {data_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # show-mapping reads only local files — no token needed
    if args.cmd == "show-mapping":
        cmd_show_mapping(data_dir)
        return

    if args.cmd == "fix-mapping":
        token = get_env("LUNCHMONEY_API_TOKEN")
        cmd_fix_mapping(data_dir, LMClient(token))
        return

    token = get_env("LUNCHMONEY_API_TOKEN")
    client = LMClient(token)

    if args.cmd == "init-mapping":
        print(f"Generating mapping.yaml for {data_dir}/\n")
        cmd_init_mapping(data_dir, client)

    elif args.cmd == "audit":
        print(f"Auditing Lunch Money against YNAB export in {data_dir}/\n")
        cmd_audit(data_dir, client)

    elif args.cmd == "import":
        print("Import not yet implemented.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
