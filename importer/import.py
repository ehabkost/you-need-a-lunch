#!/usr/bin/env python3
"""Lunch Money importer — import YNAB data into Lunch Money.

Usage:
  ./run.sh ./importer/import.py --data data/brl init-mapping
  ./run.sh ./importer/import.py --data data/brl audit
  ./run.sh ./importer/import.py --data data/brl import          # dry-run (not yet implemented)
  ./run.sh ./importer/import.py --data data/brl import --apply  # apply    (not yet implemented)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from lm_client import LMClient
from mapping import MAPPING_FILE, Mapping

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

        # 2. direct_import_linked → try to match to plaid account by institution+type
        elif a.get("direct_import_linked"):
            ynab_type = a["type"]
            lm_type_equiv = {"creditCard": "credit"}.get(ynab_type, "depository")
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
            if best_match and best_score >= 0.5:
                entry = {
                    "lm_type": "plaid",
                    "lm_id": best_match["id"],
                    "match_method": "name",
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
            score = entry.get("_match_score", 0)
            confidence = "auto-matched" if score >= 0.8 else "suggested — verify!"
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

    # lm_excluded section — prepopulate with any LM entities not referenced in mapping
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
    orphan_cats   = [c["id"] for c in lm_cats_flat if c["id"] not in mapped_cat_ids]

    lines.append("# LM entities that have no YNAB counterpart.")
    lines.append("# Add their IDs here to suppress audit errors, or map them above.")
    lines.append("lm_excluded:")

    def write_excluded(label: str, ids: list[int], lm_items: list[dict], name_key="name"):
        items_by_id = {a["id"]: a for a in lm_items}
        if ids:
            lines.append(f"  {label}:")
            for lid in ids:
                item = items_by_id.get(lid, {})
                name = item.get(name_key, "?")
                mask = f"  mask={item['mask']}" if "mask" in item else ""
                lines.append(f"  - {lid}  # {name}{mask}")
        else:
            lines.append(f"  {label}: []")

    write_excluded("manual_accounts", orphan_manual, lm_manual)
    write_excluded("plaid_accounts",  orphan_plaid,  lm_plaid)
    write_excluded("categories",      orphan_cats,   lm_cats_flat)

    out_path.write_text("\n".join(lines) + "\n")

    # summary
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
        print(f"\n  ⚠ LM has entities not in YNAB — review lm_excluded section:")
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


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import YNAB data into Lunch Money.")
    parser.add_argument("--data", metavar="DIR", required=True,
                        help="Path to YNAB export directory (e.g. data/brl)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-mapping", help="Generate initial mapping.yaml from YNAB export + LM state")
    sub.add_parser("audit", help="Verify every LM entity maps to a YNAB entity")
    p_import = sub.add_parser("import", help="Import YNAB data into LM (dry-run by default)")
    p_import.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")

    args = parser.parse_args()
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"Error: {data_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

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
