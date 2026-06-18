#!/usr/bin/env python3
"""YNAB data exporter — writes all budget data to JSON files.

Automatically does a delta update if a checkpoint exists, otherwise a full export.

Usage:
  ./prod-run.sh ./ynab/export.py                      # list budgets
  ./prod-run.sh ./ynab/export.py --budget ID          # export (auto: delta or full)
  ./prod-run.sh ./ynab/export.py --budget ID --full   # force full export
  ./prod-run.sh ./ynab/export.py --budget ID --out DIR
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://api.ynab.com/v1"
CHECKPOINT_FILE = "checkpoint.json"


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"Error: {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


# ── API client ────────────────────────────────────────────────────────────────

class YNABClient:
    def __init__(self, token):
        self._token = token
        self.request_count = 0

    def get(self, path, *, since: int | None = None, allow_404=False):
        url = f"{BASE_URL}{path}"
        if since is not None:
            url += f"?last_knowledge_of_server={since}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token}"})
        try:
            with urllib.request.urlopen(req) as resp:
                self.request_count += 1
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", 60))
                print(f"    rate limited — waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                return self.get(path, since=since, allow_404=allow_404)
            if e.code == 404 and allow_404:
                return None
            body = e.read().decode()
            print(f"HTTP {e.code} fetching {path}: {body}", file=sys.stderr)
            sys.exit(1)


# ── checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint(out_dir: Path) -> dict:
    path = out_dir / CHECKPOINT_FILE
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_checkpoint(out_dir: Path, checkpoint: dict):
    path = out_dir / CHECKPOINT_FILE
    path.write_text(json.dumps(checkpoint, indent=2))


# ── file helpers ──────────────────────────────────────────────────────────────

def load_json(out_dir: Path, name: str) -> list:
    path = out_dir / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_json(out_dir: Path, name: str, data):
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    count = f"{len(data)} records" if isinstance(data, list) else "saved"
    print(f"  → {path.name}  ({count})")


def merge_by_id(existing: list, delta: list, key="id") -> tuple[list, int, int]:
    """Upsert delta records into existing list by key. Returns (merged, updated, added)."""
    index = {r[key]: r for r in existing}
    updated = added = 0
    for record in delta:
        if record[key] in index:
            updated += 1
        else:
            added += 1
        index[record[key]] = record
    return list(index.values()), updated, added


def merge_categories(existing_groups: list, delta_groups: list) -> tuple[list, int, int]:
    """Merge a YNAB categories delta. Counts/returns are at the *category* level.

    Categories are nested inside groups, and YNAB's delta returns each changed group as a
    container carrying ONLY its changed categories — so replacing whole groups (as merge_by_id
    would) silently drops every unchanged sibling. Instead, upsert by category id and re-nest
    each category under its current category_group_id, which also handles category moves and
    deletions (deleted categories come back with deleted=True and are kept, per CLAUDE.md).
    """
    group_meta: dict = {}   # group_id -> group dict (its .categories is rebuilt below)
    cats: dict = {}         # category_id -> category dict
    for g in existing_groups:
        group_meta[g["id"]] = g
        for c in g.get("categories", []):
            cats[c["id"]] = c
    updated = added = 0
    for g in delta_groups:
        group_meta[g["id"]] = g
        for c in g.get("categories", []):
            if c["id"] in cats:
                updated += 1
            else:
                added += 1
            cats[c["id"]] = c
    # Re-nest categories under their current group.
    by_group: dict = {}
    for c in cats.values():
        by_group.setdefault(c.get("category_group_id"), []).append(c)
    merged = []
    for gid, g in group_meta.items():
        g = dict(g)
        g["categories"] = by_group.get(gid, [])
        merged.append(g)
    return merged, updated, added


# ── export helpers ────────────────────────────────────────────────────────────

def _resolve_since(out_dir: Path, name: str, checkpoint: dict, ck_key: str) -> int | None:
    """Delta cursor for a resource, or None to force a full fetch.

    A stored cursor is only honoured when the on-disk file still exists; if the file is
    missing we ignore it and re-fetch in full, so deleting <name>.json triggers a clean
    rebuild of just that resource (other resources keep doing cheap deltas).
    """
    since = checkpoint.get(ck_key)
    if since is not None and not (out_dir / f"{name}.json").exists():
        print(f"  ({name}.json missing — full re-fetch)")
        return None
    return since


def fetch_simple(client, path, data_key, out_dir, name, checkpoint, ck_key, *,
                 allow_404=False, merge_fn=merge_by_id):
    """Fetch a simple list endpoint, merge with existing, update checkpoint."""
    since = _resolve_since(out_dir, name, checkpoint, ck_key)
    resp = client.get(path, since=since, allow_404=allow_404)
    if resp is None:
        print(f"  (not available)")
        return

    records = resp["data"].get(data_key, [])
    server_knowledge = resp["data"].get("server_knowledge")

    if since is not None:
        existing = load_json(out_dir, name)
        merged, updated, added = merge_fn(existing, records)
        save_json(out_dir, name, merged)
        print(f"    {added} added, {updated} updated (was {len(existing)})")
    else:
        save_json(out_dir, name, records)

    if server_knowledge is not None:
        checkpoint[ck_key] = server_knowledge


def fetch_months(client, budget_id, out_dir, checkpoint):
    """Fetch budget months. Delta only re-fetches months whose summaries changed."""
    since = _resolve_since(out_dir, "months", checkpoint, "months")
    resp = client.get(f"/budgets/{budget_id}/months", since=since)
    changed_summaries = resp["data"]["months"]
    server_knowledge = resp["data"].get("server_knowledge")

    changed_summaries.sort(key=lambda m: m["month"])

    existing_months = load_json(out_dir, "months")
    index = {m["month"]: m for m in existing_months}

    if since is not None:
        print(f"  {len(changed_summaries)} month(s) changed since last sync")
    else:
        print(f"  {len(changed_summaries)} months total")

    for i, summary in enumerate(changed_summaries):
        month_str = summary["month"]
        label = month_str[:7]
        suffix = f"({i + 1}/{len(changed_summaries)})" if len(changed_summaries) > 1 else ""
        print(f"  {label} {suffix}")
        detail = client.get(f"/budgets/{budget_id}/months/{month_str}")["data"]["month"]
        index[month_str] = detail

    months = sorted(index.values(), key=lambda m: m["month"])
    save_json(out_dir, "months", months)

    if server_knowledge is not None:
        checkpoint["months"] = server_knowledge


# ── full export / delta update ────────────────────────────────────────────────

def run_export(client: YNABClient, budget_id: str, budget: dict, out_dir: Path, update: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint(out_dir) if update else {}

    if update and not checkpoint:
        print("No checkpoint found — running full export instead.\n")

    print("Accounts...")
    fetch_simple(client, f"/budgets/{budget_id}/accounts", "accounts",
                 out_dir, "accounts", checkpoint, "accounts")

    print("Payees...")
    fetch_simple(client, f"/budgets/{budget_id}/payees", "payees",
                 out_dir, "payees", checkpoint, "payees")

    print("Payee locations...")
    # payee_locations does not support delta
    resp = client.get(f"/budgets/{budget_id}/payee_locations")
    locations = resp["data"]["payee_locations"]
    save_json(out_dir, "payee_locations", locations)

    print("Categories...")
    fetch_simple(client, f"/budgets/{budget_id}/categories", "category_groups",
                 out_dir, "categories", checkpoint, "categories", merge_fn=merge_categories)

    print("Transactions...")
    fetch_simple(client, f"/budgets/{budget_id}/transactions", "transactions",
                 out_dir, "transactions", checkpoint, "transactions")

    print("Scheduled transactions...")
    fetch_simple(client, f"/budgets/{budget_id}/scheduled_transactions", "scheduled_transactions",
                 out_dir, "scheduled_transactions", checkpoint, "scheduled_transactions")

    print("Budget months...")
    fetch_months(client, budget_id, out_dir, checkpoint)

    print("Money movements...")
    fetch_simple(client, f"/budgets/{budget_id}/money_movements", "money_movements",
                 out_dir, "money_movements", checkpoint, "money_movements", allow_404=True)

    print("Money movement groups...")
    fetch_simple(client, f"/budgets/{budget_id}/money_movement_groups", "money_movement_groups",
                 out_dir, "money_movement_groups", checkpoint, "money_movement_groups", allow_404=True)

    save_checkpoint(out_dir, checkpoint)
    print(f"  → {CHECKPOINT_FILE}  (server_knowledge per resource)")

    metadata = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "budget_id": budget_id,
        "budget_name": budget["name"],
        "currency": budget["currency_format"]["iso_code"],
        "api_requests": client.request_count,
        "mode": "update" if update and checkpoint else "full",
    }
    save_json(out_dir, "export_metadata", metadata)

    print(f"\nDone. {client.request_count} API requests.")


# ── entry point ───────────────────────────────────────────────────────────────

def list_budgets(client: YNABClient):
    budgets = client.get("/budgets")["data"]["budgets"]
    print("Available budgets:\n")
    for b in budgets:
        print(f"  {b['id']}  {b['name']:<30}  ({b['currency_format']['iso_code']})")
    print("\nRun with --budget ID to export.")


def main():
    parser = argparse.ArgumentParser(description="Export YNAB budget data to JSON files.")
    parser.add_argument("--budget", metavar="ID", help="Budget ID to export (omit to list budgets)")
    parser.add_argument("--full", action="store_true", help="Force full export, ignoring any saved checkpoint")
    parser.add_argument("--out", metavar="DIR", default="data", help="Output base directory (default: data)")
    args = parser.parse_args()

    token = get_env("YNAB_API_TOKEN")
    client = YNABClient(token)

    if not args.budget:
        list_budgets(client)
        return

    budgets = client.get("/budgets")["data"]["budgets"]
    budget = next((b for b in budgets if b["id"] == args.budget), None)
    if not budget:
        print(f"Error: budget '{args.budget}' not found.", file=sys.stderr)
        sys.exit(1)

    slug = budget["name"].lower().replace(" ", "-")
    out_dir = Path(args.out) / slug

    has_checkpoint = (out_dir / CHECKPOINT_FILE).exists() and not args.full
    mode = "delta update" if has_checkpoint else "full export"
    print(f"Budget:  {budget['name']}  ({budget['currency_format']['iso_code']})")
    print(f"Mode:    {mode}")
    print(f"Output:  {out_dir}/\n")

    run_export(client, args.budget, budget, out_dir, update=has_checkpoint)


if __name__ == "__main__":
    main()
