"""Phase 5: monthly audit of the alerts and of your own (manual) trades.

    python audit.py            # last 30 days
    python audit.py --days 60

Log your own trades in journal.csv (timestamp,mint,side,sol,notes) so the audit can check
whether you followed the rules: small size, only on alerts, never on 'no', never >20 min late.
"""
import argparse
import csv
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime

import chain
import config
import db


def pct(a, b):
    return (b - a) / a * 100 if a and b else None


def load_journal(path="journal.csv"):
    if not os.path.exists(path):
        return []
    rows = []
    for r in csv.DictReader(open(path)):
        try:
            r["ts"] = int(datetime.fromisoformat(r["timestamp"]).timestamp())
            r["sol"] = float(r["sol"])
            rows.append(r)
        except (KeyError, ValueError):
            continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    now = int(time.time())
    since = now - args.days * 86400

    alerts = db.query("SELECT * FROM alerts WHERE ts >= ? ORDER BY ts", (since,))
    prices = chain.token_market({a["mint"] for a in alerts}) if alerts else {}
    L = [f"# Audit: {datetime.now():%Y-%m-%d} (last {args.days} days)", ""]

    # --- alert quality by verdict ------------------------------------------------
    by_verdict = defaultdict(list)
    for a in alerts:
        now_price = (prices.get(a["mint"]) or {}).get("price_usd")
        a["move"] = pct(a["price_usd"], now_price)
        by_verdict[a["verdict"]].append(a)
    L += ["## Did the verdicts hold up?", "",
          "Price change from alert time to now (DexScreener). A token with no price now has likely died.", "",
          "| Verdict | Alerts | Median move | Up | Down >50% or dead |", "|---|---|---|---|---|"]
    for v in ("looks clean", "your call", "no", "info"):
        rows = by_verdict.get(v, [])
        if not rows:
            continue
        moves = [r["move"] for r in rows if r["move"] is not None]
        dead = sum(1 for r in rows if r["move"] is None or r["move"] <= -50)
        L.append(f"| {v} | {len(rows)} | {statistics.median(moves):+.0f}% | "
                 f"{sum(m > 0 for m in moves)} | {dead} |" if moves else
                 f"| {v} | {len(rows)} | n/a | 0 | {dead} |")
    L += ["", "If 'no' alerts often went up and 'looks clean' often died, tighten the triage prompt or the "
          "watchlist. If 'no' alerts mostly died, the bouncer is doing its job.", ""]

    # --- your trades vs the rules ------------------------------------------------
    journal = [j for j in load_journal() if j["ts"] >= since]
    L += ["## Your trades vs the rules", ""]
    if not journal:
        L += ["No journal.csv entries in this window. Log each manual trade there "
              "(timestamp,mint,side,sol,notes) so this section can grade your discipline.", ""]
    else:
        alerts_by_mint = defaultdict(list)
        for a in alerts:
            alerts_by_mint[a["mint"]].append(a)
        pnl = defaultdict(float)
        sizes = []
        issues = []
        for j in journal:
            pnl[j["mint"]] += j["sol"] if j["side"] == "sell" else -j["sol"]
            if j["side"] != "buy":
                continue
            sizes.append(j["sol"])
            prior = [a for a in alerts_by_mint.get(j["mint"], []) if a["ts"] <= j["ts"]]
            if not prior:
                issues.append(f"{j['timestamp']} {j['mint'][:6]}…: bought without an alert")
                continue
            last = prior[-1]
            if last["verdict"] == "no":
                issues.append(f"{j['timestamp']} {j['mint'][:6]}…: bought on a 'no' verdict")
            late = (j["ts"] - last["ts"]) / 60
            if late > config.LATE_AFTER_MIN:
                issues.append(f"{j['timestamp']} {j['mint'][:6]}…: bought {late:.0f} min after the alert (chasing)")
        L += [f"- Trades: {len(journal)} · Buys: {len(sizes)} · Median buy: "
              f"{statistics.median(sizes):.3f} SOL · Largest buy: {max(sizes):.3f} SOL" if sizes else
              f"- Trades: {len(journal)}",
              f"- Net SOL across tokens (closed and open): {sum(pnl.values()):+.3f}",
              f"- Winners / losers by token: {sum(v > 0 for v in pnl.values())} / {sum(v < 0 for v in pnl.values())}",
              ""]
        L += ["Rule breaks:", ""] + ([f"- {i}" for i in issues] or ["- None. Good discipline."]) + [""]

    # --- watchlist freshness -------------------------------------------------------
    stale = db.query("SELECT address, grade, graded_at FROM wallets WHERE watch = 1 AND "
                     "(graded_at IS NULL OR graded_at < ?)", (now - config.REGRADE_AFTER_DAYS * 86400,))
    L +=["## Watchlist", ""]
    L += ([f"- **Re-grade due**: {len(stale)} watched wallets were graded over "
           f"{config.REGRADE_AFTER_DAYS} days ago. Run grade_wallets.py with a fresh Kolscan top 50."]
          if stale else ["- Grades are fresh."]) + [""]

    out = f"audit_{datetime.now():%Y-%m}.md"
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
