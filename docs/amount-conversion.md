# Amount Conversion: YNAB Milliunits → Lunch Money Decimal Strings

## Convention summary

| System | Format | Sign |
|---|---|---|
| YNAB | integer milliunits (1000 = 1 unit) | negative = outflow, positive = inflow |
| Lunch Money v2 | decimal string, up to 4 places (e.g. `"50.0000"`) | positive = debit, negative = credit |

The sign conventions are **opposite** and the amount **must be negated** on conversion:

- An **expense** (money out) is **negative** in YNAB but **positive** in LM.
- **Income** (money in) is **positive** in YNAB but **negative** in LM.

Confirmed against real data (resolved — was Open Question 2): the Mercado purchase exports as
`-53350` milliunits in YNAB (an outflow) and must become `+53.35` in LM; the LM v2 split example
in the OpenAPI spec likewise shows a purchase as `"88.4500"` (positive = debit). This matches
`api-reference.md` ("opposite sign of YNAB — conversion required").

## Float precision

YNAB milliunits are integers. Python's `/` operator produces a float, which is exact only when
the result is representable in IEEE 754. YNAB stores whole-cent values (milliunits divisible
by 10, almost always by 1000), so `amount / 1000` is exact for real-world data. However,
relying on that silently is fragile.

**Always negate, then format the amount as a fixed-precision string at conversion time:**

```python
def milliunit_to_lm_amount(milliunits: int) -> str:
    # Negate: YNAB and LM use opposite sign conventions (see above).
    return f"{-milliunits / 1000:.4f}"
```

This rounds to 4 decimal places and produces the string LM expects — no raw float survives
into the API payload. Use this one helper for **every** amount (transactions, split children,
opening balances) so signs and split sums stay consistent.

## LM's own representation

The LM v2 API accepts and returns amounts as strings, which indicates deliberate avoidance of
float at the API boundary. The `to_base` field (currency conversion) is documented as a float
and is read-only — do not send it in POST/PUT. Any precision loss in LM's internal storage
would surface as a discrepancy during balance reconciliation.
