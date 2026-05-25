#!/usr/bin/env python3
"""Quick smoke test for YNAB API access. Run with:
  ./ynab-run.sh python test_ynab.py [budget-id]
"""
import os
import sys
import urllib.request
import json

BASE_URL = "https://api.ynab.com/v1"


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"Error: {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def ynab_get(token, path):
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


token = get_env("YNAB_API_TOKEN")
budget_id = sys.argv[1] if len(sys.argv) > 1 else None

print("--- GET /user ---")
user = ynab_get(token, "/user")["data"]["user"]
print(f"  id: {user['id']}")

print("\n--- GET /budgets ---")
budgets = ynab_get(token, "/budgets")["data"]["budgets"]
for b in budgets:
    marker = " <---" if b["id"] == budget_id else ""
    print(f"  {b['id']}  {b['name']:<30}  ({b['currency_format']['iso_code']}){marker}")

if budget_id:
    print(f"\n--- GET /budgets/{budget_id}/accounts ---")
    accounts = ynab_get(token, f"/budgets/{budget_id}/accounts")["data"]["accounts"]
    for a in accounts:
        flags = "".join([
            " [closed]" if a["closed"] else "",
            " [deleted]" if a["deleted"] else "",
            " [direct-import]" if a.get("direct_import_linked") else "",
        ])
        print(f"  {a['id']}  {a['name']:<30}  {a['type']}{flags}")
else:
    print("\nPass a budget ID as argument to also list its accounts.")
