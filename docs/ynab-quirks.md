# YNAB Data Quirks

Known deviations from expected YNAB API behaviour, discovered from real export data.

## `internal=true` on user-created category groups

**Affected field**: `category_groups[].internal`

**Expected behaviour**: `internal=true` marks system-managed groups that should not be shown to the user or imported (e.g. "Internal Master Category", "Credit Card Payments", "Hidden Categories").

**Actual behaviour**: At least one real user-created group in the CAD export, "Semi-regular Expenses", carries `internal=true` despite being a fully normal spending group with 19 ordinary categories (HVAC Filter, Oil Change, Gardening, etc.). Its categories all have `internal=false`.

**Likely cause**: The YNAB API has a `CategoryBase.original_category_group_id` field, documented as "DEPRECATED: No longer used. Value will always be null." This field was presumably how YNAB tracked which group a hidden category originally belonged to, before YNAB moved that tracking elsewhere. The `internal` flag on a group may be part of the current mechanism: YNAB may set `internal=true` on a group when all its categories have been hidden (the group temporarily "belongs to" the hidden-category system), then fail to clear the flag when categories are unhidden or new ones added. The YNAB OpenAPI spec defines `internal` only as "Whether or not the category group is internal" with no further explanation.

**Implication for the importer**: Do **not** use the `internal` flag on groups to identify system groups. Instead, a group is treated as system-only (and skipped) if it contains **no non-internal, non-deleted categories**. This correctly skips "Internal Master Category" (all cats `internal=true`) and imports "Semi-regular Expenses" (has non-internal cats) despite its `internal=true` flag. See `lunchmoney/import.py` `_build_category_plan`.
