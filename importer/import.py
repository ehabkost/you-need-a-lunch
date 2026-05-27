#!/usr/bin/env python3
"""Lunch Money importer — import YNAB data into Lunch Money.

Usage:
  ./run.sh ./importer/import.py --data data/cad fix-mapping    # create/fix mapping.yaml interactively
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
from sync_state import SyncState

LM_CACHE_FILE = "lm_cache.json"

# ── YNAB account type → LM account type ──────────────────────────────────────

YNAB_TO_LM_TYPE = {
    "checking":      "checking",
    "savings":       "savings",
    "cash":          "cash",
    "creditCard":    "credit",
    "lineOfCredit":  "loan",
    "mortgage":      "other liability",
    "autoLoan":      "other liability",
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


# ── matching helpers ──────────────────────────────────────────────────────────

def score_account_match(ynab_acc: dict, lm_acc: dict) -> tuple[float, str]:
    """Return (score 0–1, reason) for how well lm_acc matches ynab_acc."""
    note_digits = re.sub(r"\D", "", (ynab_acc.get("note") or ""))
    mask = lm_acc.get("mask", "")
    if mask and mask in note_digits:
        return 1.0, f"mask ···{mask} in note"
    lm_name = lm_acc.get("display_name") or lm_acc.get("name", "")
    ns = name_similarity(ynab_acc.get("name", ""), lm_name)
    ynab_type = ynab_acc.get("type", "")
    lm_type_equiv = {"creditCard": "credit"}.get(ynab_type, "depository")
    type_ok = lm_acc.get("type", "") == lm_type_equiv
    score = ns * 0.7 + (0.3 if type_ok else 0.0)
    return score, f"name similarity {ns:.0%}"


def _write_mapping_skeleton(path: Path, meta: dict, ynab_accounts: list,
                             ynab_groups: list, ynab_cats_by_group: dict):
    """Write a fresh mapping.yaml with all nulls (no heuristics applied)."""
    lines = [
        f"ynab_budget_id: \"{meta['budget_id']}\"",
        f"ynab_budget_name: \"{meta['budget_name']}\"",
        "",
        "accounts:",
    ]
    for a in sorted(ynab_accounts, key=lambda x: (not x["on_budget"], x["name"])):
        if a["deleted"]:
            continue
        lm_type = "plaid" if a.get("direct_import_linked") else "manual"
        lines += [f"  \"{a['id']}\":", f"    lm_type: {lm_type}",
                  "    lm_id: null", "    match_method: null", ""]
    lines += ["category_groups:"]
    for g in ynab_groups:
        lines.append(f"  \"{g['id']}\": null")
    lines += ["", "categories:"]
    for g in ynab_groups:
        for c in ynab_cats_by_group.get(g["id"], []):
            lines.append(f"  \"{c['id']}\": null")
    lines += ["", "lm_excluded:",
              "  manual_accounts: []", "  plaid_accounts: []", "  categories: []", ""]
    path.write_text("\n".join(lines))



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
    """Create mapping.yaml if missing, then interactively map unmatched LM entities to YNAB entities."""
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
    meta: dict = load_json(data_dir, "export_metadata")

    ynab_groups_tmp = [g for g in ynab_groups_raw if not g["deleted"] and not g["internal"]]
    ynab_cats_by_group_tmp: dict[str, list[dict]] = {
        g["id"]: [c for c in g.get("categories", []) if not c["deleted"] and not c["internal"]]
        for g in ynab_groups_tmp
    }

    if not mapping_path.exists():
        print(f"No mapping.yaml found — creating skeleton at {mapping_path}")
        _write_mapping_skeleton(mapping_path, meta, ynab_accounts,
                                ynab_groups_tmp, ynab_cats_by_group_tmp)
        print("  Skeleton created. Starting interactive mapping...\n")

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

            cands_raw = ynab_acc_candidates(lm_type)
            # rank by score against this LM account
            scored = sorted(
                [(score_account_match(ya, lm_acc), yid, ya) for yid, ya in cands_raw],
                key=lambda x: x[0][0], reverse=True,
            )
            cands = [(yid, ya) for (_, _), yid, ya in scored]
            reasons = {yid: reason for (_, reason), yid, _ in scored}
            if cands:
                print(f"  YNAB candidates (best match first):")
                for i, (yid, ya) in enumerate(cands, 1):
                    flags = []
                    if ya.get("closed"): flags.append("closed")
                    if ya.get("direct_import_linked"): flags.append("sync")
                    flag_s = f" ({','.join(flags)})" if flags else ""
                    score_val = scored[i-1][0][0]
                    reason = reasons[yid]
                    score_str = f"  [{score_val:.0%} — {reason}]"
                    print(f"    {i:2}.  {ya['name']}{flag_s}  [{ya['type']}]{DIM}{score_str}{RESET}")
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

            cands_raw = ynab_cat_candidates(is_group)
            cands = sorted(
                cands_raw,
                key=lambda x: name_similarity(name, x[1]["name"]),
                reverse=True,
            )
            if cands:
                print(f"  YNAB {kind} candidates (best match first):")
                for i, (yid, yc) in enumerate(cands, 1):
                    score_val = name_similarity(name, yc["name"])
                    score_str = f"  [{score_val:.0%}]" if score_val > 0 else ""
                    print(f"    {i:2}.  {yc['name']}{DIM}{score_str}{RESET}")
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


# ── import: accounts phase ────────────────────────────────────────────────────

# Actions for the account plan
_A_CREATE        = "create"         # create a new LM manual account
_A_SYNCED        = "already_synced" # already recorded in sync_state, nothing to do
_A_RECOVER       = "recover"        # found in LM by external_id, record in sync_state
_A_PLAID         = "match_plaid"    # matched to an existing LM Plaid account
_A_PLAID_RO      = "skip_plaid_ro"  # Plaid match found but read-only — skip
_A_DELETED       = "skip_deleted"   # YNAB account is deleted


def _build_account_plan(ynab_accounts: list, meta: dict, sync: SyncState,
                         lm_manual: list, lm_plaid: list) -> list:
    currency = meta["currency"].lower()

    lm_manual_by_ext = {a["external_id"]: a for a in lm_manual if a.get("external_id")}
    lm_plaid_by_norm = {normalize(a.get("display_name") or a.get("name", "")): a
                        for a in lm_plaid}

    plan = []
    for acc in sorted(ynab_accounts, key=lambda a: (not a["on_budget"], a["name"])):
        ynab_id = acc["id"]

        if acc["deleted"]:
            plan.append({"action": _A_DELETED, "acc": acc})
            continue

        existing = sync.account(ynab_id)
        if existing:
            plan.append({"action": _A_SYNCED, "acc": acc,
                         "lm_id": existing["lm_id"], "lm_type": existing["lm_type"],
                         "lm_name": existing["lm_name"]})
            continue

        # Crash recovery: already created in LM but not yet in sync_state
        if ynab_id in lm_manual_by_ext:
            lm_acc = lm_manual_by_ext[ynab_id]
            plan.append({"action": _A_RECOVER, "acc": acc, "lm_type": "manual",
                         "lm_id": lm_acc["id"], "lm_name": lm_acc["name"]})
            continue

        # Bank-synced in YNAB: try to match an existing Plaid account in LM
        if acc.get("direct_import_linked"):
            match = lm_plaid_by_norm.get(normalize(acc["name"]))
            if match:
                if match.get("allow_transaction_modification", True):
                    plan.append({"action": _A_PLAID, "acc": acc,
                                 "lm_id": match["id"],
                                 "lm_name": match.get("display_name") or match.get("name")})
                else:
                    plan.append({"action": _A_PLAID_RO, "acc": acc,
                                 "lm_id": match["id"],
                                 "lm_name": match.get("display_name") or match.get("name")})
                continue

        # Create as manual account
        lm_type = YNAB_TO_LM_TYPE.get(acc.get("type", ""), "other asset")
        custom = {"ynab_type": acc.get("type"), "ynab_on_budget": acc.get("on_budget")}
        if acc.get("direct_import_linked"):
            custom["ynab_bank_synced"] = True  # was direct-import in YNAB, no Plaid match
        if acc.get("note"):
            custom["ynab_note"] = acc["note"]

        payload: dict = {
            "name": acc["name"][:45],
            "type": lm_type,
            "balance": "0.0000",
            "currency": currency,
            "external_id": ynab_id,
            "custom_metadata": custom,
        }
        if acc.get("closed"):
            payload["status"] = "closed"

        plan.append({"action": _A_CREATE, "acc": acc, "lm_type": "manual",
                     "lm_payload": payload})

    return plan


def _print_account_plan(plan: list, apply: bool):
    counts = {k: 0 for k in (_A_CREATE, _A_SYNCED, _A_RECOVER, _A_PLAID,
                               _A_PLAID_RO, _A_DELETED)}
    for item in plan:
        counts[item["action"]] += 1

    verb = "Will" if apply else "Would"
    print(f"\n  {verb} create  {counts[_A_CREATE]:3} manual account(s)")
    if counts[_A_RECOVER]:
        print(f"  {verb} recover {counts[_A_RECOVER]:3} already-created account(s) into sync state")
    if counts[_A_PLAID]:
        print(f"  {verb} link    {counts[_A_PLAID]:3} account(s) to existing Plaid accounts")
    if counts[_A_PLAID_RO]:
        print(f"  {YELLOW}Skip     {counts[_A_PLAID_RO]:3} Plaid account(s) (read-only){RESET}")
    if counts[_A_SYNCED]:
        print(f"  Skip     {counts[_A_SYNCED]:3} already-synced account(s)")
    if counts[_A_DELETED]:
        print(f"  Skip     {counts[_A_DELETED]:3} deleted account(s)")

    if counts[_A_CREATE] == 0 and counts[_A_RECOVER] == 0 and counts[_A_PLAID] == 0:
        return

    on_budget  = [i for i in plan if i["action"] == _A_CREATE and i["acc"].get("on_budget")]
    off_budget = [i for i in plan if i["action"] == _A_CREATE and not i["acc"].get("on_budget")]

    def _print_group(label: str, items: list):
        if not items:
            return
        print(f"\n    {CYAN}{label}{RESET}")
        for item in items:
            acc = item["acc"]
            flags = []
            if acc.get("closed"):               flags.append("closed")
            if acc.get("direct_import_linked"): flags.append("was-synced")
            flag_s = f"  ({', '.join(flags)})" if flags else ""
            lm_type = YNAB_TO_LM_TYPE.get(acc.get("type", ""), "other asset")
            print(f"      {acc['name']}{flag_s}  [{acc.get('type')} → {lm_type}]")

    if on_budget or off_budget:
        print()
    _print_group("On budget",  on_budget)
    _print_group("Off budget", off_budget)

    if counts[_A_PLAID]:
        plaid_items = [i for i in plan if i["action"] == _A_PLAID]
        print(f"\n    {CYAN}Matched to Plaid{RESET}")
        for item in plaid_items:
            print(f"      {item['acc']['name']}  → LM Plaid '{item['lm_name']}'")

    if counts[_A_PLAID_RO]:
        print(f"\n    {YELLOW}Skipped (Plaid read-only — transactions cannot be added){RESET}")
        for item in (i for i in plan if i["action"] == _A_PLAID_RO):
            print(f"      {item['acc']['name']}  → LM Plaid '{item['lm_name']}'")


def phase_accounts(data_dir: Path, client: LMClient, sync: SyncState, sync_dir: Path,
                   meta: dict, apply: bool) -> int:
    """Plan and optionally execute account sync. Returns count of changes made."""
    ynab_accounts: list = load_json(data_dir, "accounts")

    print("  Fetching Lunch Money accounts...")
    lm_manual = client.get_manual_accounts()
    lm_plaid  = client.get_plaid_accounts()

    plan = _build_account_plan(ynab_accounts, meta, sync, lm_manual, lm_plaid)
    _print_account_plan(plan, apply)

    actionable = [i for i in plan if i["action"] in (_A_CREATE, _A_RECOVER, _A_PLAID)]
    if not actionable:
        print(f"\n  {GREEN}Nothing to do.{RESET}")
        return 0

    if not apply:
        print(f"\n  {DIM}(dry-run — pass --apply to create){RESET}")
        return 0

    print()
    changes = 0
    for item in plan:
        action = item["action"]
        acc    = item["acc"]

        if action == _A_CREATE:
            result = client.create_manual_account(item["lm_payload"])
            sync.set_account(acc["id"], lm_type="manual",
                             lm_id=result["id"], lm_name=result["name"])
            sync.save(sync_dir)
            print(f"  {GREEN}✓{RESET} Created  '{acc['name']}'  → LM manual {result['id']}")
            changes += 1

        elif action == _A_RECOVER:
            sync.set_account(acc["id"], lm_type="manual",
                             lm_id=item["lm_id"], lm_name=item["lm_name"])
            sync.save(sync_dir)
            print(f"  {GREEN}✓{RESET} Recovered '{acc['name']}'  → LM manual {item['lm_id']}")
            changes += 1

        elif action == _A_PLAID:
            sync.set_account(acc["id"], lm_type="plaid",
                             lm_id=item["lm_id"], lm_name=item["lm_name"])
            sync.save(sync_dir)
            print(f"  {GREEN}✓{RESET} Linked   '{acc['name']}'  → LM Plaid {item['lm_id']}")
            changes += 1

    return changes


# ── import command ────────────────────────────────────────────────────────────

def cmd_import(data_dir: Path, client: LMClient, apply: bool):
    meta: dict = load_json(data_dir, "export_metadata")

    print("Fetching Lunch Money user info...")
    me = client.get_me()
    lm_account_id = me["account_id"]

    sync, sync_dir = SyncState.load_or_create(
        data_dir,
        lm_account_id=lm_account_id,
        ynab_budget_id=meta["budget_id"],
        ynab_budget_name=meta["budget_name"],
        currency=meta["currency"].lower(),
    )

    label = f"{meta['budget_name']} ({meta['currency']})"
    mode  = "APPLYING" if apply else "DRY-RUN"
    print(BOLD + f"\nImporting {label}  [{mode}]\n" + RESET)

    print(BOLD + "── Accounts ──" + RESET)
    phase_accounts(data_dir, client, sync, sync_dir, meta, apply)

    # Future phases (categories, transactions) go here

    if not apply:
        print(f"\n{DIM}Run with --apply to execute the above changes.{RESET}\n")
    else:
        print(f"\n{GREEN}Done.{RESET}  Sync state: {sync_dir / 'sync_state.json'}\n")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import YNAB data into Lunch Money.")
    parser.add_argument("--data", metavar="DIR", required=True,
                        help="Path to YNAB export directory (e.g. data/brl)")
    sub = parser.add_subparsers(dest="cmd", required=True)

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

    if args.cmd == "audit":
        print(f"Auditing Lunch Money against YNAB export in {data_dir}/\n")
        cmd_audit(data_dir, client)

    elif args.cmd == "import":
        cmd_import(data_dir, client, apply=args.apply)


if __name__ == "__main__":
    main()
