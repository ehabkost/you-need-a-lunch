# Payee Rules Import Plan

> **Status: 🔭 v2, deferred — see [ROADMAP.md](ROADMAP.md).** Demoted from "Phase 0.5": it
> needs Playwright UI automation + email/password creds (no LM rules API), a brittler risk
> class than the API-clean importers. Revisit after v1.

## Source data

`data/tmp/ynab-catalog.json` (captured from YNAB internal API — see `docs/ynab-internal-catalog-api.md`).

Two independent YNAB features feed into this plan:

1. **`be_payee_rename_conditions`** — rename rules: raw bank string → clean payee name
   - Fields: `operator` (`Is` / `Contains`), `operand` (raw bank string), `entities_payee_id`
   - 514 active (non-tombstoned)

2. **`be_payees.auto_fill_subcategory_id`** + **`auto_fill_subcategory_enabled`** — default category per payee
   - Separate from rename rules; fires when a payee is selected in YNAB
   - Available on the same payee objects linked by `entities_payee_id`

In LM, both collapse into a single rule with up to two actions (rename + set category).

## Rule breakdown

| Type | Count | LM actions |
|---|---|---|
| Non-transfer, has category | 423 | set payee name + set category |
| Transfer, no category | 62 | set payee name (LM format) + set category = "Payment, Transfer" |
| Non-transfer, no category | 24 | set payee name only |
| Transfer, has category | 5 | set payee name (LM format) + set category = "Payment, Transfer" (override) |
| **Total** | **514** | |

Transfer payees are identified by `be_payees[].entities_account_id != null`.

## Operator mapping

| YNAB `operator` | LM rule condition |
|---|---|
| `Is` | payee name `matches exactly` |
| `Contains` | payee name `contains` |

Note: LM `matches exactly` is case-sensitive. YNAB `Is` behavior should be tested — if YNAB is case-insensitive, use `contains` with the full string as a fallback.

## Transfer payee name mapping

YNAB transfer payees have a `name` like `"Transfer : Edu Checking CIBC 🧾"`.
In LM, we use the same name convention for imported transfer transactions (both legs get payee = the YNAB transfer payee name).

For the rename rule action, use the YNAB payee name as-is. This keeps it consistent with imported transactions and makes the Transfer Management Tool's job easier later (see `docs/future-tools.md`).

Transfer rule category action is always `"Payment, Transfer"` (the LM-native special category created in Phase 0 — `sync_state.special_categories.payment_transfer`). Any YNAB auto_fill category on a transfer payee is ignored.

## Implementation

### Scripts

#### `ynab/extract_payee_rules.py`

Reads `data/tmp/ynab-catalog.json`, outputs `data/payee_rules.json`:

```json
[
  {
    "operator": "Is" | "Contains",
    "operand": "raw bank string",
    "payee_name": "clean payee name",
    "is_transfer": true | false,
    "lm_category_name": "Groceries" | "Payment, Transfer" | null
  }
]
```

Logic:
1. Load `be_payees` as a map by id
2. Load `be_subcategories` as a map by id (for category name lookup)
3. For each active (non-tombstoned) rename condition:
   - Look up payee via `entities_payee_id`
   - `is_transfer` = `payee.entities_account_id != null`
   - If `is_transfer`: `lm_category_name = "Payment, Transfer"`
   - Else if `payee.auto_fill_subcategory_enabled and auto_fill_subcategory_id`: look up category name from `be_subcategories`
   - Else: `lm_category_name = null`
4. Write `data/payee_rules.json`

#### `lunchmoney/import_payee_rules.py`

Reads `data/payee_rules.json`. Uses Playwright to drive the LM web UI.

**Dry-run mode (default):**

```
514 payee rename rules to import
  423  rename + set category
   67  rename + set category = "Payment, Transfer"  (transfer payees)
   24  rename only

Sample:
  [exact]    "PAY 10644587576 DIGITAL OCEAN C"  → "DigitalOcean Bonus"  [Retirement Extra]
  [contains] "H & M"                             → "H & M"              [Roupas 👗]
  [exact]    "INTERNET TRANSFER 000000115647"    → "Transfer : Edu Credit CIBC Visa" [Payment, Transfer]

Proceed? [y/N]
```

**Apply mode (`--apply`):**

1. Load `data/rules_import_state.json` (created on first run, updated incrementally)
2. Launch Playwright browser (headless by default, `--headed` for debugging)
3. Log in to `my.lunchmoney.app` with `LUNCHMONEY_EMAIL` + `LUNCHMONEY_PASSWORD`
4. For each rule not already in `rules_import_state.json`:
   a. Navigate to Rules page, click "Add a new rule"
   b. Set condition: payee name `contains` / `matches exactly` → `operand`
   c. Set action: set payee name → `payee_name`
   d. If `lm_category_name` is set: add action "set category" → `lm_category_name`
   e. Click "Create" (not "Create & Apply" — historical txns already have correct names)
   f. Record `operand` → `{created: true, rule_id: ...}` in `rules_import_state.json`
   g. Sleep ~1s between rules to avoid UI overload
5. Print summary: created / skipped (already existed) / failed

**Idempotency / crash recovery:** `rules_import_state.json` keyed by `operand`. Re-run skips already-created rules.

### Auth

`LUNCHMONEY_EMAIL` and `LUNCHMONEY_PASSWORD` added to `.env.production` (and stored in 1Password).
`LUNCHMONEY_API_TOKEN` already exists for the API-based importers.

### Dependencies

```
playwright
```

Add to `requirements.txt` + install in `.venv`:
```sh
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

## Phase placement

**Deferred to v2** (see [ROADMAP.md](ROADMAP.md)). When picked up: it only matters for
*future* Plaid-synced transactions (historical YNAB transactions already carry correct payee
names), so it can run any time after Phase 0 creates `sync_state` with
`special_categories.payment_transfer` — there is no need to slot it before Phase 1.

## Open questions

- **LM `matches exactly` case sensitivity**: verify whether YNAB `Is` is case-insensitive. If so, prefer `contains` for `Is` rules, or add both variants.
- **Category name conflicts**: some YNAB category names in `be_subcategories` may not match LM category names exactly after Phase 0 import (e.g. if a category was renamed). Look up category via `sync_state` by YNAB category id rather than by name.
- **Rules list scraping for deduplication**: to skip already-existing rules on re-run without relying solely on `rules_import_state.json`, consider scraping the LM rules page at startup.
