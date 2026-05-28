# Planned Post-Migration Tools

Reference: `data/docs/discord-thread-about-transfers-etc.txt` — LM Discord support thread covering how users handle transfers and the lack of automatic transaction matching in LM. Key takeaways:
- LM does **not** auto-match/merge transactions from different sources (Plaid vs manual, Plaid vs 3rd-party). They remain separate entries. Clara (LM support) confirmed there is no API for linking them either (as of May 2026).
- The recommended transfer workflow is: categorize both legs to "Payment, Transfer" (which is exclude-from-budget + exclude-from-totals), then optionally group them.
- "Create transfer" from an imported transaction creates a *new* third transaction — wrong if the other leg is already imported. Do not use that UI for already-imported transfers.

## Transaction Matching Tool

After import, the user may have manually entered transactions in LM that duplicate ones coming in via Plaid sync (or YNAB-imported ones). This tool helps identify and safely resolve duplicates.

### Candidate Signals

Use in combination, never alone:

1. **YNAB memo / transfer account ID**: YNAB sometimes records the transfer counterpart account ID in `transfer_account_id`. If the memo or custom_metadata on the imported transaction references the other account, that is a strong signal.
2. **Plaid-provided fields**: Plaid transactions carry a `pending` flag, a `pending_transaction_id` (links pending→posted), and sometimes a `transaction_id` that matches across sources. If both candidates share a Plaid transaction ID or pending link, that is a near-certain match.
3. **Amount + date proximity**: Same absolute amount on the same account, dates within ±3 days. Necessary but not sufficient on its own — high false-positive risk for common round amounts.
4. **Payee similarity**: Normalized payee name overlap (after stripping emoji/punctuation). Boosts confidence but not reliable alone.
5. **Account match**: Both transactions must be on the same LM account. A match across different accounts is almost certainly wrong.

### False-Positive Prevention

- Never auto-merge without explicit user confirmation per pair.
- Require at least two independent signals before surfacing a candidate (e.g. amount+date is not enough by itself; add payee or a Plaid field).
- Show the full transaction details of both sides before asking the user to confirm.
- If the user declines a pair, record it as "rejected" in local state so it is never re-surfaced.

### Why Grouping Alone Doesn't Work for Deduplication

The group parent's `amount` is the *sum* of all children. Grouping two ×$50 transactions yields a $100 group — double-counting. LM has no merge/replace API (confirmed by LM support as of May 2026).

### Grouping Workaround

One possible workaround is to zero out the manual transaction's amount and keep the real amount only on the Plaid transaction, then group both — the group total then equals the Plaid amount. This avoids double-counting without deleting either leg, at the cost of a zero-amount orphan entry.

### Preferred Resolution

Delete the manual transaction and store its full JSON in `custom_metadata.matched_manual` on the surviving (Plaid/imported) transaction. This gives a complete audit trail and allows manual recovery if the match turns out to be wrong. Note: `custom_metadata` is capped at 4096 characters when stringified; a typical transaction object is ~500–800 chars so this should fit comfortably.

### Feature Tracking

Native auto-linking of manually created transactions to synced ones is tracked upstream at https://feedback.lunchmoney.app/transactions/p/auto-link-manually-created-transactions-to-synced-transactions — if that ships, the matching tool may become unnecessary.

## Transfer Management Tool

Transfers in YNAB create two linked transaction legs (one per account). In LM the recommended approach (per LM support) is:
1. Categorize both legs to the "Payment, Transfer" category (exclude-from-budget + exclude-from-totals).
2. Optionally group them using the LM transaction group API to keep them visually linked.

This tool automates that workflow:

- **Orphan detection**: find transactions in the "Payment, Transfer" category (or with `transfer_account_id` set in YNAB metadata) that have no group partner.
- **Pair matching**: same absolute amount, opposite sign, dates within ±3 days, on accounts that are plausibly a transfer pair (both owned by user). Apply the same false-positive caution as the matching tool above.
- **Dry-run summary**: show proposed pairs and ungrouped orphans before applying anything.
- **On apply**: set category to "Payment, Transfer" on both legs if not already set, then create an LM transaction group linking the two.
- **Do not use "create transfer"** on already-imported transactions — that generates a spurious third entry.

## Shared Concern: Silent Budget Leaks via Exclude-From-Totals Categories

Both `Payment, Transfer` and `Tracking (off-budget)` rely on the same LM mechanism — `exclude_from_budget` + `exclude_from_totals` — to keep certain transactions out of budget math. That mechanism is also the failure mode: any transaction that lands in either category disappears from budget totals, and an accidental mis-categorization (or a property toggle on the category itself) silently leaks real spending out of the budget.

The Transfer Management Tool and the Tracking-Category Audit Tool below should share infrastructure for this:

- **Common checks**: category-property drift (both exclude flags still set), unexpected volume spikes month-over-month, account/category coherence (txn's source account makes sense for the category).
- **Common "suspicious" surface**: a unified report listing every transaction currently excluded from budget totals, with the reason (transfer vs tracking vs other exclude-from-totals categories) and a confidence score that it belongs there.
- **Shared primitives**: a `find_excluded_transactions(period)` helper, a `category_properties_unchanged(category_id, expected)` check, and the volume-baseline computation should live in one module both tools call.

Treat this as one auditing subsystem with two specialized front-ends, not two independent tools.

## Tracking-Category Audit Tool

The import maps YNAB off-budget ("Tracking") account transactions to a dedicated `Tracking (off-budget)` LM category with `exclude_from_budget=true` and `exclude_from_totals=true` (see [transaction-import-plan.md](transaction-import-plan.md)). Because that category is invisible to budgets and totals, accidental misuse is silent: money can leave a real on-budget account and never show up in any budget bucket.

This tool periodically audits the category to catch drift:

- **Account/category coherence**: every transaction in `Tracking (off-budget)` should be on an LM account whose source YNAB account had `on_budget=false` (recorded in `custom_metadata.ynab_on_budget` or derivable from the account's `external_id`). Flag any transaction in the tracking category that sits on an on-budget account.
- **Inverse check**: any transaction on a tracking-source account that is *not* in the tracking category (and not in `Payment, Transfer`) is also suspicious — either the user re-categorized it deliberately or the importer mis-routed it. Surface both.
- **Category-property drift**: verify the category still has both `exclude_from_budget` and `exclude_from_totals` set. A user who accidentally toggles either flag would silently start double-counting tracking activity in budget totals.
- **Volume check**: compare the count and absolute-sum of tracking-category transactions per month against the prior month. A large spike likely means a real on-budget transaction landed there by mistake.
- **Output**: a report with proposed re-categorizations; never auto-apply. The user picks per-row.

This is a maintenance tool, not part of import — run it on demand or on a schedule against the live LM account.

## Account-Merging Helper Tool

LM supports merging accounts ([Managing Accounts](https://support.lunchmoney.app/setup/accounts/managing-accounts)), and the multi-currency strategy assumes the user will restructure accounts after import. Doing this by hand is risky for an imported budget: the source account's `external_id` and `custom_metadata` may or may not survive the merge (unverified — see open question in [transaction-import-plan.md](transaction-import-plan.md)), which breaks future re-sync.

This tool wraps the merge with pre/post hooks that protect sync_state:

### Pre-merge

- Read both accounts via the LM API; capture their full `external_id`, `custom_metadata`, balance, balance history, and transaction count.
- Read local `sync_state` and identify which YNAB account(s) map to each LM account.
- Show a dry-run summary:
  - Source → destination
  - Whether either is Plaid-synced (warn: source must be manual, or LM merge flow differs)
  - Currency match (warn loudly on mismatch — merging across currencies destroys balance-history coherence)
  - Which YNAB accounts in `sync_state` will need their mapping re-pointed
  - Counts of transactions, recurring items, and rules that will migrate
- Require explicit confirmation.

### Merge

- Use the LM API to merge (or instruct the user to perform the UI merge if no API exists — verify before building).
- Capture the destination account's post-merge `external_id` and `custom_metadata`.

### Post-merge

- Detect what survived: did the destination inherit the source's `external_id`? Did transaction-level `custom_metadata.ynab_id` survive on migrated transactions? (Sample-check a few.)
- If `external_id` was lost: write the source's `external_id` onto the destination via the LM API. If LM rejects (uniqueness conflict — e.g. destination already had its own), fall back to storing it in `custom_metadata.ynab_external_ids` as a list.
- Update local `sync_state`: re-point every YNAB account that mapped to the source so it now maps to the destination's LM ID. Record the merge in a `sync_state.merges` audit log with timestamp, source ID, destination ID, and which metadata fields were preserved/lost.
- Re-run the deduplication check from the importer against the destination account, since merge can introduce duplicates if both accounts had overlapping manual entries.

### Safety

- Refuse to merge if either account has unsynced changes in `sync_state` (pending import).
- Always print a recovery hint: which JSON file holds the pre-merge snapshot, so the user can manually reconstruct mappings if something goes wrong.
- Never delete the local pre-merge snapshot — merges are irreversible in LM and the snapshot is the only audit trail.
