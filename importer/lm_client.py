"""Lunch Money v2 API client."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.lunchmoney.dev/v2"
BATCH_SIZE = 500  # max transactions per POST /transactions


class LMClient:
    def __init__(self, token: str):
        self._token = token
        self.request_count = 0

    def _request(self, method: str, path: str, body=None, params: dict | None = None):
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                self.request_count += 1
                return None if resp.status == 204 else json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", 60))
                print(f"    rate limited — waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                return self._request(method, path, body=body, params=params)
            print(f"HTTP {e.code} {method} {path}: {e.read().decode()}", file=sys.stderr)
            sys.exit(1)

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def post(self, path, body):
        return self._request("POST", path, body=body)

    def put(self, path, body):
        return self._request("PUT", path, body=body)

    def delete(self, path):
        return self._request("DELETE", path)

    # ── reads ─────────────────────────────────────────────────────────────────

    def get_me(self) -> dict:
        resp = self.get("/me")
        # Real API wraps in {"user": {...}}, mock API returns directly
        return resp.get("user", resp)

    def get_manual_accounts(self) -> list[dict]:
        return self.get("/manual_accounts")["manual_accounts"]

    def get_plaid_accounts(self) -> list[dict]:
        return self.get("/plaid_accounts")["plaid_accounts"]

    def get_categories(self) -> list[dict]:
        return self.get("/categories")["categories"]

    def get_transactions(self, *, include_metadata=True, **kwargs) -> list[dict]:
        """Fetch all matching transactions, paginating automatically."""
        params = {k: v for k, v in kwargs.items() if v is not None}
        if include_metadata:
            params["include_metadata"] = "true"
        params["limit"] = BATCH_SIZE
        all_txns: list[dict] = []
        offset = 0
        while True:
            params["offset"] = offset
            batch = self.get("/transactions", params=params).get("transactions", [])
            all_txns.extend(batch)
            if len(batch) < BATCH_SIZE:
                break
            offset += BATCH_SIZE
        return all_txns

    # ── writes ────────────────────────────────────────────────────────────────

    def create_manual_account(self, data: dict) -> dict:
        return self.post("/manual_accounts", data)

    def update_manual_account(self, account_id: int, data: dict) -> dict:
        return self.put(f"/manual_accounts/{account_id}", data)

    def create_category(self, data: dict) -> dict:
        return self.post("/categories", data)

    def update_category(self, category_id: int, data: dict) -> dict:
        return self.put(f"/categories/{category_id}", data)

    def insert_transactions(self, transactions: list[dict]) -> dict:
        """Insert in batches of 500. Returns aggregated response."""
        inserted, skipped = [], []
        for i in range(0, len(transactions), BATCH_SIZE):
            resp = self.post("/transactions", {"transactions": transactions[i:i + BATCH_SIZE]})
            inserted.extend(resp.get("transactions", []))
            skipped.extend(resp.get("skipped_duplicates", []))
        return {"transactions": inserted, "skipped_duplicates": skipped}

    def upsert_budget(self, start_date: str, category_id: int, amount: str, currency: str):
        return self.put("/budgets", {
            "start_date": start_date,
            "category_id": category_id,
            "amount": amount,
            "currency": currency,
        })
