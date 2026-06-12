"""Controlled live probe: what happens when several transactions that are all
matched to the SAME recurring item are deleted and then re-inserted together in
a single POST /transactions request?

Builds on the `recurring-notes-dropped-on-put` finding (LM's recurring matcher
drops `notes` at insert time). Here we check the multi-row / single-batch path:

  * Do all of them get re-matched to a recurring item again?
  * Is the `notes` we POST persisted, or silently dropped on every row?
  * Does LM dedup any of them (skipped_duplicates) when re-inserting matching rows
    in one request?
  * Does explicitly setting `recurring_id` on insert change the notes outcome?

Runs against whatever LUNCHMONEY_API_TOKEN is in the environment (use test-run.sh,
the test account). DELETE is irreversible — the re-inserted rows get fresh IDs.

Modes
-----
  survey                       list recurring groups on the account (counts + notes)
  run --recurring-id N         delete N's members, re-insert all in one POST, report
        [--count K]            cap how many members to operate on (default 5, min 3)
        [--set-recurring-id]   also send recurring_id=N on each insert (vs. auto-match)
        [--keep]               don't attempt anything after; just leave the new rows

A before/after snapshot is written under data/tmp/ for the writeup.
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lunchmoney"))
from lm_client import LMClient  # noqa: E402
from lm_api_types_generated import InsertTransactionObject  # noqa: E402

SNAP_DIR = Path(__file__).resolve().parent.parent / "data" / "tmp"


def _client() -> LMClient:
    token = os.environ.get("LUNCHMONEY_API_TOKEN")
    if not token:
        sys.exit("LUNCHMONEY_API_TOKEN not set (use ./test-run.sh)")
    return LMClient(token)


def cmd_survey(c: LMClient) -> None:
    txns = c.get_transactions()
    groups: dict[int, list[Any]] = defaultdict(list)
    for t in txns:
        if t.recurring_id is not None:
            groups[t.recurring_id].append(t)
    print(f"{len(txns)} transactions total; "
          f"{sum(len(v) for v in groups.values())} matched to "
          f"{len(groups)} recurring item(s)\n")
    print(f"{'recurring_id':>14}  {'count':>5}  {'w/notes':>7}  payee (sample)")
    for rid, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        with_notes = sum(1 for m in members if m.notes)
        sample = next((m.payee for m in members if m.payee), "")
        flag = "  <-- 3-5, good candidate" if 3 <= len(members) <= 5 else ""
        print(f"{rid:>14}  {len(members):>5}  {with_notes:>7}  {sample!r}{flag}")


def _account_id(t: Any) -> tuple[str, int]:
    if t.manual_account_id is not None:
        return "manual_account_id", t.manual_account_id
    if t.plaid_account_id is not None:
        return "plaid_account_id", t.plaid_account_id
    raise SystemExit(f"txn {t.id} has neither manual nor plaid account id")


def _to_insert(t: Any, *, notes: str, recurring_id: int | None) -> InsertTransactionObject:
    acct_field, acct_id = _account_id(t)
    kwargs: dict[str, Any] = dict(
        date=date.fromisoformat(str(t.date)[:10]),
        amount=t.amount,
        currency=t.currency,
        payee=t.payee,
        category_id=t.category_id,
        notes=notes,
        status=t.status if t.status in ("reviewed", "unreviewed") else "unreviewed",
        external_id=t.external_id,
        custom_metadata=t.custom_metadata or None,
        recurring_id=recurring_id,
    )
    kwargs[acct_field] = acct_id
    return InsertTransactionObject(**kwargs)


def _row(t: Any) -> dict[str, Any]:
    return {
        "id": t.id, "date": str(t.date)[:10], "amount": str(t.amount),
        "payee": t.payee, "notes": t.notes, "status": t.status,
        "recurring_id": t.recurring_id, "external_id": t.external_id,
        "created_at": str(t.created_at), "updated_at": str(t.updated_at),
    }


def cmd_run(c: LMClient, recurring_id: int, count: int, set_recurring_id: bool) -> None:
    tag = str(int(time.time()))
    marker = f"probe-reinsert-{tag}"
    all_txns = c.get_transactions()
    members = [t for t in all_txns if t.recurring_id == recurring_id]
    members = [t for t in members if not t.is_split_parent and not t.is_group_parent
               and not getattr(t, "split_parent_id", None)
               and not getattr(t, "group_parent_id", None)]
    if len(members) < 3:
        sys.exit(f"recurring_id {recurring_id} has only {len(members)} simple member(s); "
                 "need >=3. Run `survey` to pick another.")
    members = members[:count]

    before = [_row(t) for t in members]
    print(f"Recurring item {recurring_id}: operating on {len(members)} member(s)")
    for r in before:
        print(f"  id={r['id']}  {r['date']}  amt={r['amount']:>10}  "
              f"notes={r['notes']!r}  payee={r['payee']!r}")

    print(f"\nMarker notes to POST: {marker!r}"
          f"   (set_recurring_id={set_recurring_id})")

    print("\nStep 1: DELETE each member...")
    for t in members:
        c.delete_transaction(t.id)
        print(f"  deleted {t.id}")

    print("\nStep 2: POST all of them back in ONE request...")
    inserts = [_to_insert(t, notes=marker,
                          recurring_id=recurring_id if set_recurring_id else None)
               for t in members]
    resp = c.insert_transactions(inserts)
    print(f"  inserted={len(resp.transactions)}  "
          f"skipped_duplicates={len(resp.skipped_duplicates or [])}")
    if resp.skipped_duplicates:
        print(f"  skipped_duplicates payload: {resp.skipped_duplicates}")

    new_ids = [t.id for t in resp.transactions]
    print(f"  new ids: {new_ids}")

    print("\nStep 3: GET each new row back and inspect...")
    after = []
    for nid in new_ids:
        t = c.get_transaction(nid)
        after.append(_row(t))
        dropped = "NOTES DROPPED" if t.notes != marker else "notes kept"
        rematch = (f"re-matched recurring_id={t.recurring_id}"
                   if t.recurring_id is not None else "NOT re-matched")
        print(f"  id={t.id}  notes={t.notes!r}  [{dropped}]  [{rematch}]  status={t.status}")

    n_dropped = sum(1 for r in after if r["notes"] != marker)
    n_rematched = sum(1 for r in after if r["recurring_id"] is not None)
    print("\n── Verdict ──")
    print(f"  inserted {len(after)}/{len(members)} "
          f"(skipped_duplicates={len(resp.skipped_duplicates or [])})")
    print(f"  notes dropped on {n_dropped}/{len(after)} re-inserted rows")
    print(f"  re-matched to a recurring item: {n_rematched}/{len(after)}")

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = SNAP_DIR / f"reinsert_{recurring_id}_{tag}.json"
    snap.write_text(json.dumps({
        "recurring_id": recurring_id, "marker": marker,
        "set_recurring_id": set_recurring_id,
        "skipped_duplicates": resp.skipped_duplicates,
        "before": before, "after": after,
    }, indent=2, default=str))
    print(f"\nSnapshot written to {snap}")


def _months_back(n: int, day: int) -> date:
    """First day of (this month - n), pinned to `day`."""
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(day, 28))


def cmd_seed(c: LMClient, account_id: int, count: int, amount: str,
             payee: str | None, day: int, same_note: bool = False) -> None:
    """Insert `count` fictional monthly transactions with a never-seen payee/amount/notes
    in a single POST, to see whether LM's detector spins up a NEW recurring item (and
    whether it then drops the notes). With same_note, every row gets the identical memo
    (to test whether memo uniqueness affects detection)."""
    tag = str(int(time.time()))
    payee = payee or f"Zentaro Probe Co {tag[-5:]}"
    marker_base = f"probe-seed-{tag}"
    dates = [_months_back(i, day) for i in range(count - 1, -1, -1)]
    print(f"Seeding {count} monthly txns into account {account_id}"
          f"  (same_note={same_note})")
    print(f"  payee={payee!r}  amount={amount}  notes~={marker_base!r}\n")

    inserts = []
    for i, d in enumerate(dates):
        note = marker_base if same_note else f"{marker_base}-{i}"
        inserts.append(InsertTransactionObject(
            date=d, amount=amount, currency="cad", payee=payee,
            notes=note, status="unreviewed", manual_account_id=account_id,
            external_id=f"{marker_base}-{i}",
            custom_metadata={"probe_memo": note, "ynab_id": f"fake-{i}"},
        ))
        print(f"  {d}  notes={note!r}  custom_metadata.probe_memo={note!r}")

    print("\nPOST all in ONE request...")
    resp = c.insert_transactions(inserts)
    print(f"  inserted={len(resp.transactions)}  "
          f"skipped_duplicates={len(resp.skipped_duplicates or [])}")
    new_ids = [t.id for t in resp.transactions]
    print(f"  new ids: {new_ids}")

    print("\nImmediate read-back (does custom_metadata survive recurring linking?):")
    after = []
    for nid in new_ids:
        t = c.get_transaction(nid)
        after.append(_row(t))
        cm = t.custom_metadata or {}
        print(f"  id={t.id}  recurring_id={t.recurring_id}  notes={t.notes!r}  "
              f"custom_metadata={cm!r}")
    n_match = sum(1 for r in after if r["recurring_id"] is not None)
    print(f"\n  matched to a recurring item at insert: {n_match}/{len(after)}")
    print("  (LM's create-new-recurring detector is async; use `recheck` later.)")

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = SNAP_DIR / f"seed_{tag}.json"
    snap.write_text(json.dumps({
        "kind": "seed", "marker": marker_base, "payee": payee,
        "amount": amount, "account_id": account_id,
        "note": "per-row marker is f'{marker}-{i}'; recheck compares prefix",
        "before": [], "after": after,
    }, indent=2, default=str))
    print(f"\nSnapshot written to {snap}")


def cmd_recheck(c: LMClient, snapshot: Path) -> None:
    """Re-read the rows a `run` produced and report whether the async recurring
    matcher has since attached a recurring_id and/or dropped the marker notes."""
    data = json.loads(snapshot.read_text())
    marker = data["marker"]
    ids = [r["id"] for r in data["after"]]
    print(f"Re-checking {len(ids)} row(s) from {snapshot.name}")
    print(f"  marker notes posted: {marker!r}\n")
    n_dropped = n_rematched = 0
    for nid in ids:
        t = c.get_transaction(nid)
        # seed snapshots use a per-row marker `{marker}-{i}`; match by prefix
        dropped = not (t.notes or "").startswith(marker)
        n_dropped += dropped
        n_rematched += t.recurring_id is not None
        print(f"  id={t.id}  notes={t.notes!r}  "
              f"recurring_id={t.recurring_id}  updated_at={t.updated_at}  "
              f"[{'NOTES DROPPED' if dropped else 'notes kept'}]")
    print("\n── Recheck verdict ──")
    print(f"  notes now dropped on {n_dropped}/{len(ids)} rows")
    print(f"  now matched to a recurring item: {n_rematched}/{len(ids)}")


def _latest_snapshot() -> Path:
    snaps = sorted([*SNAP_DIR.glob("reinsert_*.json"), *SNAP_DIR.glob("seed_*.json")],
                   key=lambda p: p.stat().st_mtime)
    if not snaps:
        sys.exit(f"no reinsert_*/seed_*.json snapshots in {SNAP_DIR}")
    return snaps[-1]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("survey", help="list recurring groups on the account")
    pr = sub.add_parser("run", help="delete a recurring group and re-insert in one POST")
    pr.add_argument("--recurring-id", type=int, required=True)
    pr.add_argument("--count", type=int, default=5)
    pr.add_argument("--set-recurring-id", action="store_true",
                    help="send recurring_id on insert instead of letting LM auto-match")
    ps = sub.add_parser("seed", help="insert fictional monthly txns to trigger a NEW recurring item")
    ps.add_argument("--account-id", type=int, required=True)
    ps.add_argument("--count", type=int, default=6)
    ps.add_argument("--amount", default="-13.57", help="signed amount string (negative=outflow)")
    ps.add_argument("--payee", help="payee (default: a unique fictional name)")
    ps.add_argument("--day", type=int, default=14, help="day-of-month for the cadence")
    ps.add_argument("--same-note", action="store_true",
                    help="give every row the identical memo (test memo-uniqueness)")
    pc = sub.add_parser("recheck", help="re-read a run's rows to see if the matcher caught up")
    pc.add_argument("--snapshot", help="path to a reinsert_*/seed_*.json (default: latest)")
    args = p.parse_args()

    c = _client()
    if args.cmd == "survey":
        cmd_survey(c)
    elif args.cmd == "run":
        cmd_run(c, args.recurring_id, max(3, args.count), args.set_recurring_id)
    elif args.cmd == "seed":
        cmd_seed(c, args.account_id, max(3, args.count), args.amount, args.payee,
                 args.day, args.same_note)
    elif args.cmd == "recheck":
        snap = Path(args.snapshot) if args.snapshot else _latest_snapshot()
        cmd_recheck(c, snap)


if __name__ == "__main__":
    main()
