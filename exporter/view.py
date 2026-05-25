#!/usr/bin/env python3
"""View YNAB exported data.

Usage:
  ./exporter/view.py <data-dir> accounts
  ./exporter/view.py <data-dir> budget [YYYY-MM]
"""
import json
import sys
from pathlib import Path

# ── ANSI colours (suppressed when not a TTY) ─────────────────────────────────

USE_COLOR = sys.stdout.isatty()

def _c(code): return f"\033[{code}m" if USE_COLOR else ""

RESET  = _c(0)
BOLD   = _c(1)
DIM    = _c(2)
RED    = _c(31)
GREEN  = _c(32)
YELLOW = _c(33)
CYAN   = _c(36)
WHITE  = _c(97)

def colored(text, *codes): return "".join(codes) + text + RESET


# ── formatting helpers ────────────────────────────────────────────────────────

def fmt(milliunits, formatted_str):
    """Return coloured formatted amount string."""
    if milliunits < 0:
        return colored(formatted_str, RED)
    if milliunits > 0:
        return colored(formatted_str, GREEN)
    return colored(formatted_str, DIM)


def _visible_width(text) -> int:
    """Terminal display width of text, stripping ANSI codes and measuring Unicode width."""
    import re
    from wcwidth import wcswidth
    stripped = re.sub(r"\033\[[0-9;]*m", "", text)
    w = wcswidth(stripped)
    return w if w >= 0 else len(stripped)


def col(text, width, right=False):
    """Fixed-width column padded to terminal display width."""
    pad = max(0, width - _visible_width(text))
    if right:
        return " " * pad + text
    return text + " " * pad


# ── data loading ──────────────────────────────────────────────────────────────

def load(data_dir: Path, name: str):
    return json.loads((data_dir / f"{name}.json").read_text())


# ── accounts view ─────────────────────────────────────────────────────────────

ACCOUNT_TYPE_LABELS = {
    "checking":      "Checking",
    "savings":       "Savings",
    "cash":          "Cash",
    "creditCard":    "Credit Card",
    "otherAsset":    "Other Asset",
    "otherLiability":"Other Liability",
}

def view_accounts(data_dir: Path):
    meta     = load(data_dir, "export_metadata")
    accounts = load(data_dir, "accounts")

    active = [a for a in accounts if not a["deleted"] and not a["closed"]]
    on_budget  = [a for a in active if     a["on_budget"]]
    off_budget = [a for a in active if not a["on_budget"]]

    print(colored(f"\nACCOUNTS — {meta['budget_name']} ({meta['currency']})\n", BOLD))

    def print_group(group_accounts, header):
        if not group_accounts:
            return
        print(colored(f"  {header}", BOLD, CYAN))
        # header row
        print("  " + col(colored("Name", DIM), 38)
              + col(colored("Cleared",   DIM), 16, right=True)
              + col(colored("Uncleared", DIM), 16, right=True)
              + col(colored("Balance",   DIM), 16, right=True))
        print("  " + colored("─" * 84, DIM))

        by_type: dict[str, list] = {}
        for a in group_accounts:
            by_type.setdefault(a["type"], []).append(a)

        for atype, accs in sorted(by_type.items()):
            if len(by_type) > 1:
                print(f"    {colored(ACCOUNT_TYPE_LABELS.get(atype, atype), DIM, BOLD)}")
            for a in sorted(accs, key=lambda x: x["name"]):
                di = colored(" ⇄", CYAN) if a.get("direct_import_linked") else ""
                name = col(a["name"] + di, 36)
                cleared   = col(fmt(a["cleared_balance"],   a["cleared_balance_formatted"]),   14, right=True)
                uncleared = col(fmt(a["uncleared_balance"], a["uncleared_balance_formatted"]), 14, right=True)
                balance   = col(fmt(a["balance"],           a["balance_formatted"]),           14, right=True)
                indent = "      " if len(by_type) > 1 else "    "
                print(f"{indent}{name}  {cleared}  {uncleared}  {balance}")
        print()

    print_group(on_budget,  "On Budget")
    print_group(off_budget, "Off Budget")

    # totals
    def total(accs, field):
        return sum(a[field] for a in accs)

    all_active = on_budget + off_budget
    if all_active:
        sample = all_active[0]
        # derive currency symbol from a formatted value
        import re
        sym = re.sub(r"[\d,.\-]", "", sample["balance_formatted"]).strip() or ""
        def mfmt(v): return f"{sym}{v/1000:,.2f}" if v >= 0 else f"-{sym}{abs(v)/1000:,.2f}"

        tot_b  = total(all_active, "balance")
        tot_cl = total(all_active, "cleared_balance")
        tot_un = total(all_active, "uncleared_balance")
        print(colored("  Net totals (all active accounts)", DIM))
        print(f"    Cleared:    {fmt(tot_cl, mfmt(tot_cl))}")
        print(f"    Uncleared:  {fmt(tot_un, mfmt(tot_un))}")
        print(f"    Balance:    {colored(mfmt(tot_b), BOLD)}")
    print()


# ── budget view ───────────────────────────────────────────────────────────────

def view_budget(data_dir: Path, month_arg: str | None):
    meta   = load(data_dir, "export_metadata")
    months = load(data_dir, "months")

    # find target month
    if month_arg:
        target = month_arg if "-01" in month_arg else month_arg + "-01"
        month  = next((m for m in months if m["month"] == target), None)
        if not month:
            print(f"Month {month_arg} not found.", file=sys.stderr)
            print("Available:", ", ".join(m["month"][:7] for m in months))
            sys.exit(1)
    else:
        # most recent month with any activity or assignments
        month = next(
            (m for m in reversed(months)
             if any(c["budgeted"] or c["activity"] for c in m.get("categories", []))),
            months[-1],
        )

    label = month["month"][:7]
    print(colored(f"\nBUDGET — {meta['budget_name']} ({meta['currency']}) — {label}\n", BOLD))

    # month summary line
    def sfmt(v, fv): return fmt(v, fv)
    print("  "
          + f"Income {sfmt(month['income'], month['income_formatted'])}  "
          + f"Assigned {sfmt(month['budgeted'], month['budgeted_formatted'])}  "
          + f"Activity {sfmt(month['activity'], month['activity_formatted'])}  "
          + f"To Be Assigned {sfmt(month['to_be_budgeted'], month['to_be_budgeted_formatted'])}")
    print()

    categories = [c for c in month.get("categories", [])
                  if not c["deleted"] and not c["hidden"] and not c["internal"]]

    # group by category_group_name, preserving order
    groups: dict[str, list] = {}
    for c in categories:
        groups.setdefault(c["category_group_name"], []).append(c)

    COL_NAME = 36
    COL_AMT  = 14

    hdr = ("  " + col(colored("Category",  DIM), COL_NAME)
           + col(colored("Assigned",  DIM), COL_AMT, right=True)
           + col(colored("Activity",  DIM), COL_AMT, right=True)
           + col(colored("Balance",   DIM), COL_AMT, right=True))
    print(hdr)
    print("  " + colored("═" * (COL_NAME + COL_AMT * 3 + 2), DIM))
    print()

    for group_name, cats in groups.items():
        # skip groups where every category has zeros
        if not any(c["budgeted"] or c["activity"] or c["balance"] for c in cats):
            continue

        print(colored(f"  {group_name}", BOLD, CYAN))
        print("  " + colored("─" * (COL_NAME + COL_AMT * 3 + 2), DIM))

        g_budgeted = g_activity = g_balance = 0
        for c in sorted(cats, key=lambda x: x["name"]):
            if not c["budgeted"] and not c["activity"] and not c["balance"]:
                continue
            name     = col(c["name"], COL_NAME)
            assigned = col(fmt(c["budgeted"], c["budgeted_formatted"]), COL_AMT, right=True)
            activity = col(fmt(c["activity"], c["activity_formatted"]), COL_AMT, right=True)
            balance  = col(fmt(c["balance"],  c["balance_formatted"]),  COL_AMT, right=True)
            print(f"    {name}{assigned}{activity}{balance}")
            g_budgeted += c["budgeted"]
            g_activity += c["activity"]
            g_balance  += c["balance"]

        # group subtotal
        import re
        sample_fmt = cats[0]["budgeted_formatted"]
        sym = re.sub(r"[\d,.\-]", "", sample_fmt).strip() or ""
        def mfmt(v): return f"{sym}{v/1000:,.2f}" if v >= 0 else f"-{sym}{abs(v)/1000:,.2f}"
        print("  " + colored("─" * (COL_NAME + COL_AMT * 3 + 2), DIM))
        print("  " + col(colored("Subtotal", DIM, BOLD), COL_NAME + 2)
              + col(fmt(g_budgeted, mfmt(g_budgeted)), COL_AMT, right=True)
              + col(fmt(g_activity, mfmt(g_activity)), COL_AMT, right=True)
              + col(fmt(g_balance,  mfmt(g_balance)),  COL_AMT, right=True))
        print()


# ── entry point ───────────────────────────────────────────────────────────────

def usage():
    print(__doc__)
    sys.exit(1)

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        usage()

    data_dir = Path(args[0])
    if not data_dir.is_dir():
        print(f"Error: {data_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    cmd = args[1]
    if cmd == "accounts":
        view_accounts(data_dir)
    elif cmd == "budget":
        month = args[2] if len(args) > 2 else None
        view_budget(data_dir, month)
    else:
        usage()

if __name__ == "__main__":
    main()
