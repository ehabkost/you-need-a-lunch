# Lunch Money API Bug Reports

## Bug 1: POST /transactions returns 500 — `original_name` missing DB column (2026-06-02)

### Symptom

`POST /transactions` returns HTTP 500 with:

```json
{
  "message": "Undefined binding(s) detected when compiling SELECT. Undefined column(s): [original_name] query: select * from \"recurring_expenses\" where \"account_id\" = ? and \"original_name\" = ? and \"amount\" = ? and \"currency\" = ?",
  "errors": []
}
```

### Root cause

This is a **server-side LM bug**. When inserting a transaction, LM's recurring-expense matching logic runs a SQL query that references `original_name` as a column on the `recurring_expenses` table. That column does not exist in the DB schema, causing the 500.

Our importer does **not** send `original_name` in the request body (we use `model_dump(mode="json", exclude_none=True)` and never set the field). The bug is triggered regardless — LM always runs this query when inserting into an account that has recurring items.

### Reproducer

```sh
curl -s -X POST https://api.lunchmoney.dev/v2/transactions \
  -H "Authorization: Bearer $LUNCHMONEY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transactions":[{"date":"2024-01-01","amount":"10.00","payee":"Test","manual_account_id":341928}]}'
```

### Workaround

None on our side — this is entirely in LM's server. We cannot work around it by changing request shape. Need LM to add the missing column or remove it from the query.

### Status

- **Likely transient.** Re-running all 25 batches (12,026 txns total) the next day completed without error. First 8 batches skipped as duplicates (already inserted in the failed run); remaining 17 batches inserted successfully.
- Not yet reported to LM support — may have been a momentary server issue.
- Not currently blocking.
