"""Controlled live probe: does LM v2 PUT /transactions persist `notes`, and does
omitting `notes` from a PUT body clear it (full-replace) or preserve it (merge)?

Runs against whatever LUNCHMONEY_API_TOKEN is in the environment (use test-run.sh).
Operates on a single transaction and restores it at the end.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lunchmoney"))
from lm_client import LMClient  # noqa: E402

TXN_ID = int(os.environ.get("PROBE_TXN_ID", "2410817400"))


def show(label, c):
    t = c.get_transaction(TXN_ID)
    print(f"  [{label}] notes={t.notes!r}  payee={t.payee!r}  updated_at={t.updated_at}")
    return t


def main():
    token = os.environ.get("LUNCHMONEY_API_TOKEN")
    if not token:
        sys.exit("LUNCHMONEY_API_TOKEN not set")
    c = LMClient(token)

    print(f"Probing transaction {TXN_ID}\n")

    orig = show("0 initial", c)
    orig_notes = orig.notes
    orig_payee = orig.payee
    tag = str(int(time.time()))

    print("\nStep 1: PUT {notes: 'probe-set-%s'}  — does PUT write notes at all?" % tag)
    c.update_transaction(TXN_ID, {"notes": f"probe-set-{tag}"})
    s1 = show("1 after set-notes", c)

    print("\nStep 2: PUT {payee: 'probe-payee-%s'} (NO notes key) — merge or full-replace?" % tag)
    c.update_transaction(TXN_ID, {"payee": f"probe-payee-{tag}"})
    s2 = show("2 after set-payee-only", c)

    print("\n── Verdict ──")
    if s1.notes == f"probe-set-{tag}":
        print("  • PUT *does* persist notes (step 1 took effect).")
    else:
        print(f"  • PUT did NOT persist notes (step 1 notes={s1.notes!r}). <-- PUT ignores notes")
    if s1.notes and s2.notes == s1.notes:
        print("  • Omitting notes PRESERVES it -> PUT is a MERGE.")
    elif s1.notes and not s2.notes:
        print("  • Omitting notes CLEARED it -> PUT is a FULL-REPLACE. <-- explains the data loss")

    print("\nRestoring original payee/notes...")
    restore = {"payee": orig_payee}
    if orig_notes is not None:
        restore["notes"] = orig_notes
    else:
        # original was null; try to clear whatever we set
        restore["notes"] = None
    c.update_transaction(TXN_ID, restore)
    show("3 restored", c)


if __name__ == "__main__":
    main()
