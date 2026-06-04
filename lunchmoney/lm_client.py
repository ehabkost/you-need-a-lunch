"""Lunch Money v2 API client."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TypeVar, Type, Any, cast

from pydantic import BaseModel
from lm_api_types_generated import (
    CreateManualAccountRequestObject,
    UpdateManualAccountRequestObject,
    CreateCategoryRequestObject,
    UpdateCategoryRequestObject,
    UpsertBudgetRequestObject,
    InsertTransactionObject,
    InsertTransactionsResponseObject,
    ManualAccountObject,
    PlaidAccountObject,
    CategoryObject,
    TransactionObject,
    SplitTransactionObject,
)

BASE_URL = "https://api.lunchmoney.dev/v2"
BATCH_SIZE = 500  # max transactions per POST /transactions


class CategoryNameExistsError(Exception):
    """Raised when POST /categories fails because the name is already taken."""
    def __init__(self, name: str, existing_id: int):
        super().__init__(f"Category '{name}' already exists (id={existing_id})")
        self.existing_id = existing_id

T = TypeVar("T", bound=BaseModel)


class LMClient:
    def __init__(self, token: str):
        self._token = token
        self.request_count = 0

    def _request(
        self,
        method: str,
        path: str,
        body: BaseModel | dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        # Convert pydantic models to dict
        if isinstance(body, BaseModel):
            body = body.model_dump(exclude_none=True)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                self.request_count += 1
                if resp.status == 204:
                    return {}
                return cast("dict[str, Any]", json.loads(resp.read()))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", 60))
                print(f"    rate limited — waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                return self._request(method, path, body=body, params=params)
            raw = e.read().decode()
            if e.code == 400 and method == "POST" and path == "/categories":
                try:
                    payload = json.loads(raw)
                    for err in payload.get("errors", []):
                        if err.get("code") == "CATEGORY_NAME_ALREADY_EXISTS":
                            raise CategoryNameExistsError(
                                err["requested_name"], err["existing_category_id"]
                            )
                except (json.JSONDecodeError, KeyError):
                    pass
            print(f"HTTP {e.code} {method} {path}: {raw}", file=sys.stderr)
            sys.exit(1)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def get_list(self, path: str, key: str, model: Type[T], params: dict[str, Any] | None = None) -> list[T]:
        resp = self._request("GET", path, params=params)
        return [model(**item) for item in resp[key]]

    def post(self, path: str, body: BaseModel, model: Type[T]) -> T:
        return model(**self._request("POST", path, body=body))

    def put(self, path: str, body: BaseModel, model: Type[T]) -> T:
        return model(**self._request("PUT", path, body=body))

    def delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)

    # ── reads ─────────────────────────────────────────────────────────────────

    def get_me(self) -> dict[str, Any]:
        resp = self.get("/me")
        # Real API wraps in {"user": {...}}, mock API returns directly
        return cast("dict[str, Any]", resp.get("user", resp))

    def get_manual_accounts(self) -> list[ManualAccountObject]:
        return self.get_list("/manual_accounts", "manual_accounts", ManualAccountObject)

    def get_plaid_accounts(self) -> list[PlaidAccountObject]:
        return self.get_list("/plaid_accounts", "plaid_accounts", PlaidAccountObject)

    def get_categories(self) -> list[CategoryObject]:
        return self.get_list("/categories", "categories", CategoryObject)

    def get_transactions(self, *, include_metadata: bool = True, **kwargs: Any) -> list[TransactionObject]:
        """Fetch all matching transactions, paginating automatically."""
        params: dict[str, Any] = {k: v for k, v in kwargs.items() if v is not None}
        if include_metadata:
            params["include_metadata"] = "true"
        params["limit"] = BATCH_SIZE
        all_txns: list[TransactionObject] = []
        offset = 0
        while True:
            params["offset"] = offset
            resp = self.get("/transactions", params=params)
            batch = resp.get("transactions", [])
            all_txns.extend([TransactionObject(**t) for t in batch])
            if len(batch) < BATCH_SIZE:
                break
            offset += BATCH_SIZE
        return all_txns

    # ── writes ────────────────────────────────────────────────────────────────

    def create_manual_account(self, data: CreateManualAccountRequestObject) -> ManualAccountObject:
        return self.post("/manual_accounts", data, ManualAccountObject)

    def update_manual_account(self, account_id: int, data: UpdateManualAccountRequestObject) -> ManualAccountObject:
        return self.put(f"/manual_accounts/{account_id}", data, ManualAccountObject)

    def create_category(self, data: CreateCategoryRequestObject) -> CategoryObject:
        return self.post("/categories", data, CategoryObject)

    def update_category(self, category_id: int, data: UpdateCategoryRequestObject) -> CategoryObject:
        return self.put(f"/categories/{category_id}", data, CategoryObject)

    def insert_transactions(self, transactions: list[InsertTransactionObject]) -> InsertTransactionsResponseObject:
        """Insert in batches of 500. Returns aggregated response."""
        inserted: list[TransactionObject] = []
        skipped: list[Any] = []
        for i in range(0, len(transactions), BATCH_SIZE):
            batch = transactions[i:i + BATCH_SIZE]
            body = {"transactions": [t.model_dump(mode="json", exclude_none=True) for t in batch]}
            resp = self._request("POST", "/transactions", body=body)
            inserted.extend([TransactionObject(**t) for t in resp.get("transactions", [])])
            skipped.extend(resp.get("skipped_duplicates", []))
        return InsertTransactionsResponseObject(transactions=inserted, skipped_duplicates=skipped)

    def unsplit_transaction(self, transaction_id: int) -> None:
        """DELETE /transactions/split/{id} — restores a split parent to a regular transaction."""
        self._request("DELETE", f"/transactions/split/{transaction_id}")

    def split_transaction(self, transaction_id: int,
                          child_transactions: list[SplitTransactionObject]) -> TransactionObject:
        """POST /transactions/split/{id} — split a parent into children."""
        body = {"child_transactions": [c.model_dump(mode="json", exclude_none=True) for c in child_transactions]}
        resp = self._request("POST", f"/transactions/split/{transaction_id}", body=body)
        return TransactionObject(**resp)

    def get_transaction(self, transaction_id: int, *,
                        include_children: bool = False) -> TransactionObject:
        params: dict[str, Any] = {}
        if include_children:
            params["include_split_parents"] = "true"
            params["include_children"] = "true"
        resp = self.get(f"/transactions/{transaction_id}", params=params or None)
        return TransactionObject(**resp)

    def update_transaction(self, transaction_id: int, data: dict[str, Any]) -> TransactionObject:
        resp = self._request("PUT", f"/transactions/{transaction_id}", body=data)
        return TransactionObject(**resp)

    def upsert_budget(self, start_date: str, category_id: int, amount: str, currency: str) -> dict[str, Any]:
        # Pass as dict since types don't match (pydantic coercion handles conversion)
        return self._request("PUT", "/budgets", body={
            "start_date": start_date,
            "category_id": category_id,
            "amount": amount,
            "currency": currency,
        })
