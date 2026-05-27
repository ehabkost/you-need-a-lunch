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
