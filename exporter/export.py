#!/usr/bin/env python3
"""YNAB data exporter — writes all budget data to JSON files.

Usage:
  ./ynab-run.sh ./exporter/export.py                    # list budgets
  ./ynab-run.sh ./exporter/export.py --budget ID        # export to data/<budget-name>/
  ./ynab-run.sh ./exporter/export.py --budget ID --out ./my-dir
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


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"Error: {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


class YNABClient:
    def __init__(self, token):
        self._token = token
        self.request_count = 0

    def get(self, path, *, allow_404=False):
        url = f"{BASE_URL}{path}"
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
                return self.get(path, allow_404=allow_404)
            if e.code == 404 and allow_404:
                return None
            body = e.read().decode()
            print(f"HTTP {e.code} fetching {path}: {body}", file=sys.stderr)
            sys.exit(1)


def save(out_dir: Path, name: str, data):
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    count = f"{len(data)} records" if isinstance(data, list) else "saved"
    print(f"  → {path.name}  ({count})")


def export_budget(client: YNABClient, budget_id: str, budget: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Accounts...")
    accounts = client.get(f"/budgets/{budget_id}/accounts")["data"]["accounts"]
    save(out_dir, "accounts", accounts)

    print("Payees...")
    payees = client.get(f"/budgets/{budget_id}/payees")["data"]["payees"]
    save(out_dir, "payees", payees)

    print("Payee locations...")
    locations = client.get(f"/budgets/{budget_id}/payee_locations")["data"]["payee_locations"]
    save(out_dir, "payee_locations", locations)

    print("Categories...")
    category_groups = client.get(f"/budgets/{budget_id}/categories")["data"]["category_groups"]
    save(out_dir, "categories", category_groups)

    print("Transactions...")
    transactions = client.get(f"/budgets/{budget_id}/transactions")["data"]["transactions"]
    save(out_dir, "transactions", transactions)

    print("Scheduled transactions...")
    scheduled = client.get(f"/budgets/{budget_id}/scheduled_transactions")["data"]["scheduled_transactions"]
    save(out_dir, "scheduled_transactions", scheduled)

    print("Budget months...")
    month_summaries = client.get(f"/budgets/{budget_id}/months")["data"]["months"]
    # Sort oldest-first so progress is easy to follow
    month_summaries.sort(key=lambda m: m["month"])
    months_detail = []
    for i, summary in enumerate(month_summaries):
        month_str = summary["month"]  # YYYY-MM-01
        label = month_str[:7]
        print(f"  {label}  ({i + 1}/{len(month_summaries)})")
        detail = client.get(f"/budgets/{budget_id}/months/{month_str}")["data"]["month"]
        months_detail.append(detail)
    save(out_dir, "months", months_detail)

    print("Money movements...")
    resp = client.get(f"/budgets/{budget_id}/money_movements", allow_404=True)
    if resp is not None:
        movements = resp["data"].get("money_movements", [])
        save(out_dir, "money_movements", movements)
    else:
        print("  (not available for this budget)")

    print("Money movement groups...")
    resp = client.get(f"/budgets/{budget_id}/money_movement_groups", allow_404=True)
    if resp is not None:
        groups = resp["data"].get("money_movement_groups", [])
        save(out_dir, "money_movement_groups", groups)
    else:
        print("  (not available for this budget)")

    metadata = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "budget_id": budget_id,
        "budget_name": budget["name"],
        "currency": budget["currency_format"]["iso_code"],
        "api_requests": client.request_count,
    }
    save(out_dir, "export_metadata", metadata)

    print(f"\nDone. {client.request_count} API requests.")


def list_budgets(client: YNABClient):
    budgets = client.get("/budgets")["data"]["budgets"]
    print("Available budgets:\n")
    for b in budgets:
        print(f"  {b['id']}  {b['name']:<30}  ({b['currency_format']['iso_code']})")
    print("\nRun with --budget ID to export.")


def main():
    parser = argparse.ArgumentParser(description="Export YNAB budget data to JSON files.")
    parser.add_argument("--budget", metavar="ID", help="Budget ID to export (omit to list budgets)")
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

    print(f"Budget:  {budget['name']}  ({budget['currency_format']['iso_code']})")
    print(f"Output:  {out_dir}/\n")

    export_budget(client, args.budget, budget, out_dir)


if __name__ == "__main__":
    main()
