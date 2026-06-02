# YNAB Internal Catalog API

The YNAB web app uses an internal API endpoint (`/api/v1/catalog`) that returns much richer data than the public API, including **payee renaming rules** (`be_payee_rename_conditions`).

## Endpoint

```
POST https://app.ynab.com/api/v1/catalog
```

## Key headers required

```
content-type: application/x-www-form-urlencoded; charset=UTF-8
x-requested-with: XMLHttpRequest
x-ynab-api-version: 2026-01-01
x-ynab-device-os: web
x-ynab-device-app-version: 26.75.1
x-session-token: <from browser session>
x-castle-request-token: <from browser session>
Cookie: _ynab_api_session=<session cookie>
```

## Request body

```
operation_name=syncFamilyData&request_data={"family_id":"<YOUR_FAMILY_ID>","starting_device_knowledge":0,"ending_device_knowledge":0,"device_knowledge_of_server":0,"schema_version":4,"schema_version_of_knowledge":4}
```

- `family_id`: found in the request payload (not the same as budget/plan ID)
- Setting all knowledge values to 0 returns the full dataset

## Response structure (`changed_entities`)

| Key | Description |
|---|---|
| `be_payees` | All payees: `id`, `name`, `internal_name`, `auto_detect_flag` |
| `be_payee_rename_conditions` | Renaming rules: `id`, `entities_payee_id`, `operator`, `operand`, `is_tombstone` |
| `be_accounts` | Accounts |
| `be_transactions` | All transactions |
| `be_scheduled_transactions` | Scheduled transactions |
| `be_monthly_subcategory_budgets` | Budget allocations |
| `be_master_categories` / `be_subcategories` | Category hierarchy |
| ... | (many more) |

## Payee rename conditions format

```json
{
  "id": "uuid",
  "entities_payee_id": "uuid → be_payees[].id",
  "is_tombstone": false,
  "operator": "Is | Contains",
  "operand": "raw bank string to match"
}
```

- `Is` → exact match (maps to LM "matches exactly")
- `Contains` → substring match (maps to LM "contains")
- `is_tombstone: true` → deleted rule, skip

## Authentication

**This endpoint does NOT accept the public YNAB API Bearer token.** Attempting to use `Authorization: Bearer <token>` returns:

```json
{"error":{"id":"invalid_session_token","message":"Unauthorized: missing, invalid, or expired authentication token"}}
```

Authentication is session-based (browser login), entirely separate from the public API's token system. You need:

- `x-session-token` header — obtained from a live browser session
- `_ynab_api_session` cookie — set by the YNAB web app after login
- `x-castle-request-token` header — a bot-detection token generated client-side

There is no known way to obtain these programmatically without a browser (headless or real). Options:

| Approach | Notes |
|---|---|
| Manual DevTools capture | One-time, session expires — good enough for a migration |
| Headless browser (Playwright/Puppeteer) | Requires storing YNAB password, not just an API token |
| Public YNAB API | Does not expose payee renaming rules at all |

For a one-time migration, capturing the request from DevTools is the practical path.

## How to obtain a fresh session

1. Open browser DevTools → Network tab
2. Log in to app.ynab.com
3. Filter requests for `catalog`
4. Copy the request as cURL (right-click → Copy → Copy as cURL)
5. Extract `_ynab_api_session` cookie and `x-session-token` header
6. Sessions expire — re-capture when needed

## Migration use case

514 active payee rename conditions found in the exported data (`data/tmp/ynab-catalog.json`).
These can be used to create equivalent LM rules:
- Condition: payee name `contains`/`matches exactly` → `operand`
- Action: set payee name → looked up from `be_payees` via `entities_payee_id`

**To do:** Check if LM API supports creating rules programmatically; if not, rules must be created manually or via browser automation.
