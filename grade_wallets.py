"""Phases 1 & 2: shortlist and grade wallets.

    python grade_wallets.py candidates.txt            # fetch 30d history, filter junk, grade, pick watchlist
    python grade_wallets.py candidates.txt --skip-fetch   # re-grade from stored history

candidates.txt: one wallet address per line, optional label after a space
(e.g. the top 50 from the Kolscan 30-day leaderboard).
"""
import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations

import brain
import chain
import config
import db
from swaps import parse_swaps

GRADE_ORDER = ["A", "A-", "B", "C", "D", "F"]


def load_candidates(path):
    out = {}
    for line in open(path):
        parts = line.strip().split(None, 1)
        if parts and not parts[0].startswith("#"):
            out[parts[0]] = parts[1] if len(parts) > 1 else ""
    return out


def fetch(candidates, since):
    for i, (addr, label) in enumerate(candidates.items(), 1):
        print(f"[{i}/{len(candidates)}] fetching {addr[:6]}… ", end="", flush=True)
        db.upsert_wallet(addr, label=label)
        try:
            txs = chain.wallet_history(addr, since)
            baby = not chain.has_activity_before(addr, since)
        except Exception as e:
            print(f"error: {e}")
            continue
        n = 0
        for tx in txs:
            for t in parse_swaps(tx, {addr}):
                db.insert_trade(t)
                n += 1
        db.upsert_wallet(addr, excluded_reason="baby wallet (no history before window)" if baby else None)
        print(f"{n} swaps{' (baby wallet)' if baby else ''}")


def per_token_stats(trades, market):
    """Aggregate one wallet's swaps per token."""
    by_mint = defaultdict(list)
    for t in trades:
        by_mint[t["mint"]].append(t)
    rows = []
    for mint, ts in by_mint.items():
        buys = [t for t in ts if t["side"] == "buy" and t["quote_symbol"] == "SOL"]
        sells = [t for t in ts if t["side"] == "sell" and t["quote_symbol"] == "SOL"]
        if not buys:
            continue  # sells of tokens bought before the window can't be scored
        bought_sol = sum(t["quote_amount"] for t in buys)
        bought_tok = sum(t["token_amount"] for t in buys)
        sold_sol = sum(t["quote_amount"] for t in sells)
        sold_tok = sum(t["token_amount"] for t in sells)
        sold_pct = min(1.0, sold_tok / bought_tok) if bought_tok else 0
        first = min(buys, key=lambda t: t["ts"])
        m = market.get(mint) or {}
        launch = m.get("pair_created_ts")
        rows.append({
            "token": m.get("symbol") or mint[:6],
            "mint": mint,
            "bought_sol": round(bought_sol, 3),
            "sold_sol": round(sold_sol, 3),
            "sold_pct": round(sold_pct, 2),
            "realized_pnl_sol": round(sold_sol - bought_sol * sold_pct, 3),
            "mins_after_launch": round((first["ts"] - launch) / 60, 1) if launch else None,
            "first_buy_slot": first["slot"],
            "hour_utc": datetime.fromtimestamp(first["ts"], timezone.utc).hour,
            "venue": first["source"],
            "buys": len(buys), "sells": len(sells),
        })
    return rows


def wallet_summary(addr, rows):
    pnl = [r["realized_pnl_sol"] for r in rows]
    wins = sorted((p for p in pnl if p > 0), reverse=True)
    total_win = sum(wins)
    buy_sizes = [r["bought_sol"] / r["buys"] for r in rows if r["buys"]]
    entry = [r["mins_after_launch"] for r in rows if r["mins_after_launch"] is not None]
    return {
        "tokens_traded": len(rows),
        "profitable_tokens": len(wins),
        "realized_pnl_sol": round(sum(pnl), 2),
        "top_token_share_of_wins": round(wins[0] / total_win, 2) if total_win else None,
        "win_rate": round(len(wins) / len(rows), 2) if rows else 0,
        "median_buy_sol": round(statistics.median(buy_sizes), 3) if buy_sizes else None,
        "median_mins_after_launch": round(statistics.median(entry), 1) if entry else None,
        "avg_sold_pct": round(statistics.mean(r["sold_pct"] for r in rows), 2) if rows else 0,
    }


def same_block_clusters(rows_by_wallet):
    """Wallet pairs that bought the same token in the same slot >= N times (likely one person)."""
    keys = {w: {(r["mint"], r["first_buy_slot"]) for r in rows} for w, rows in rows_by_wallet.items()}
    pairs = []
    for a, b in combinations(keys, 2):
        shared = len(keys[a] & keys[b])
        if shared >= config.SAME_BLOCK_MIN_SHARED:
            pairs.append((a, b, shared))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates")
    ap.add_argument("--skip-fetch", action="store_true")
    args = ap.parse_args()
    if not config.HELIUS_API_KEY or not config.ANTHROPIC_API_KEY:
        sys.exit("Set HELIUS_API_KEY and ANTHROPIC_API_KEY in .env first.")

    candidates = load_candidates(args.candidates)
    since = int(time.time()) - config.LOOKBACK_DAYS * 86400
    if not args.skip_fetch:
        fetch(candidates, since)

    # --- stats -----------------------------------------------------------------
    trades = defaultdict(list)
    for t in db.query("SELECT * FROM trades WHERE ts >= ? AND live = 0", (since,)):
        if t["wallet"] in candidates:
            trades[t["wallet"]].append(t)
    all_mints = {t["mint"] for ts in trades.values() for t in ts}
    print(f"\nLooking up launch times for {len(all_mints)} tokens…")
    market = chain.token_market(all_mints)

    rows_by_wallet, summaries = {}, {}
    for addr in candidates:
        rows_by_wallet[addr] = per_token_stats(trades.get(addr, []), market)
        summaries[addr] = wallet_summary(addr, rows_by_wallet[addr])

    # --- junk filter -----------------------------------------------------------
    excluded = {r["address"]: r["excluded_reason"] for r in db.query(
        "SELECT address, excluded_reason FROM wallets WHERE excluded_reason LIKE 'baby%'")
        if r["address"] in candidates}
    for addr, s in summaries.items():
        if addr in excluded:
            continue
        if s["tokens_traded"] == 0:
            excluded[addr] = "no scorable swaps in window"
        elif s["profitable_tokens"] < config.MIN_PROFITABLE_TOKENS:
            excluded[addr] = f"one-hit wonder ({s['profitable_tokens']} profitable tokens)"
        elif (s["top_token_share_of_wins"] or 0) > config.ONE_HIT_SHARE:
            excluded[addr] = f"one-hit wonder ({s['top_token_share_of_wins']:.0%} of wins from one token)"
    for a, b, shared in same_block_clusters({w: r for w, r in rows_by_wallet.items() if w not in excluded}):
        # keep the better performer of a same-block pair, drop the other
        loser = a if summaries[a]["realized_pnl_sol"] < summaries[b]["realized_pnl_sol"] else b
        other = b if loser == a else a
        excluded.setdefault(loser, f"same-block buyer with {other[:6]}… ({shared} shared slots)")

    for addr in candidates:
        db.upsert_wallet(addr, excluded_reason=excluded.get(addr), watch=0,
                         median_buy_sol=summaries[addr]["median_buy_sol"],
                         stats_json=json.dumps(summaries[addr]))
    survivors = [a for a in candidates if a not in excluded]
    print(f"{len(survivors)} of {len(candidates)} wallets survive the junk filter.")
    if not survivors:
        sys.exit("Nothing left to grade. Try a longer list or looser thresholds.")

    # --- grade with Claude -----------------------------------------------------
    payload = []
    for addr in survivors:
        rows = sorted(rows_by_wallet[addr], key=lambda r: abs(r["realized_pnl_sol"]), reverse=True)[:40]
        payload.append({"address": addr, "label": candidates[addr], "summary": summaries[addr],
                        "tokens": [{k: v for k, v in r.items() if k not in ("mint", "first_buy_slot")}
                                   for r in rows]})
    print(f"Grading {len(payload)} wallets with {config.GRADING_MODEL}…")
    result = brain.grade_wallets(payload)

    now = int(time.time())
    graded = {g["address"]: g for g in result["wallets"] if g["address"] in candidates}
    for addr, g in graded.items():
        db.upsert_wallet(addr, grade=g["grade"], reason=g["why"], flag=g["flag"], graded_at=now)

    follow = [f["address"] for f in result["follow"] if f["address"] in graded]
    ranked = sorted((g for g in graded.values() if g["flag"] == "none" and g["grade"] in ("A", "A-", "B")),
                    key=lambda g: GRADE_ORDER.index(g["grade"]))
    never = {f["address"] for f in result["never"]}
    watch = [a for a in dict.fromkeys(follow + [g["address"] for g in ranked])
             if a not in never][:config.WATCHLIST_MAX]
    for addr in watch:
        db.upsert_wallet(addr, watch=1)

    write_report(candidates, summaries, excluded, graded, result, watch)
    print(f"\nWatchlist: {len(watch)} wallets. Report written to grades_report.md")
    print("Next: python setup_webhook.py")


def write_report(candidates, summaries, excluded, graded, result, watch):
    L = [f"# Wallet grades: {datetime.now():%Y-%m-%d}", "",
         f"Window: last {config.LOOKBACK_DAYS} days. Watchlist ({len(watch)}):", ""]
    for a in watch:
        g = graded[a]
        L.append(f"- **{g['grade']}** `{a}` {candidates[a]}: {g['why']}")
    L += ["", "## Would alert on", ""] + [f"- `{f['address']}`: {f['reason']}" for f in result["follow"]]
    L += ["", "## Would never follow", ""] + [f"- `{f['address']}`: {f['reason']}" for f in result["never"]]
    L += ["", "## All graded", "", "| Wallet | Grade | Flag | PnL (SOL) | Tokens | Why |", "|---|---|---|---|---|---|"]
    for a, g in sorted(graded.items(), key=lambda kv: GRADE_ORDER.index(kv[1]["grade"])):
        s = summaries[a]
        L.append(f"| `{a[:8]}…` | {g['grade']} | {g['flag']} | {s['realized_pnl_sol']} | "
                 f"{s['tokens_traded']} | {g['why']} |")
    L += ["", "## Removed by junk filter", ""] + [f"- `{a}`: {r}" for a, r in excluded.items()]
    open("grades_report.md", "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
