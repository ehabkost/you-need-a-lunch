# YNAB4 Historical Import (Future Idea)

Raw data is in `data/ynab4/YNAB/`. The most useful bundle is
`My Budget Before Fresh Start on 2016-12-22~4591CDD7.ynab4` — 12,408 BRL
transactions from 2012-08-10 to 2019-11-30, covering ~5 years before the
YNAB5 `brl` export starts (Dec 2017).

## What would need to happen

- Write a YNAB4 reader (`ynab4/export.py`?) that parses `Budget.yfull` into
  the same intermediate JSON format as the YNAB5 exporter, or a compatible
  variant
- Map YNAB4 data model to YNAB5: accounts, categories, payees, transactions,
  subtransactions (splits)
- Handle the overlap period (2017-12-11 onwards) carefully — transactions
  in that range likely exist in both YNAB4 and YNAB5 exports
- The `brl` importer could then be run with `--since` pointing at the YNAB4
  data for the pre-2017 window

## Balance consistency (critical)

Importing historical data must not change current account or category balances.
The YNAB5 export likely has an opening balance transaction per account that
encodes all pre-2017 history as a lump sum. Importing YNAB4 transactions on top
of it would double-count. The tool needs to:

1. Find the existing opening balance transaction for each account in LM
2. Replace it (or adjust it) so the historical detail substitutes the lump sum
3. Handle the YNAB4→YNAB5 handoff date carefully to avoid double-importing
   transactions that exist in both exports

## GnuCash (even older history)

A GnuCash archive covering data from ~2003 also exists (not yet analyzed).
GnuCash uses XML or SQLite — different format from YNAB4. It mostly predates
the YNAB4 data, so the same balance-consistency problem applies at the
GnuCash→YNAB4 handoff point. To be analyzed later.

## Other bundles

Small/short-lived budgets probably not worth importing:
- EUR, CZK, GBP — short trips 2013–2014
- Mae — separate person's budget (2015–2016)
- USD — 6 transactions
