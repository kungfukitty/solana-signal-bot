"""The Nerves: decide whether a live trade deserves an alert, triage it, and post to Discord.

Nothing here ever places a trade. Alerts are advice; you press the button yourself.
"""
import logging
import time

import httpx

import brain
import chain
import config
import db
from swaps import parse_swaps

log = logging.getLogger("nerves")
VERDICT_RANK = {"looks clean": 0, "your call": 1, "no": 2}
COLORS = {"looks clean": 0x2ECC71, "your call": 0xF1C40F, "no": 0xE74C3C, "info": 0x95A5A6}


def worst(*verdicts):
    return max((v for v in verdicts if v), key=VERDICT_RANK.get)


# --- triggers ------------------------------------------------------------------
def check_trigger(trade, watched, now=None):
    """Return the alert kind for this trade, or None. Mirrors the video's two filters."""
    now = now or int(time.time())
    since = now - config.CLUSTER_WINDOW_MIN * 60
    marks = ",".join("?" * len(watched))
    rows = db.query(
        f"SELECT DISTINCT wallet FROM trades WHERE mint = ? AND side = ? AND ts >= ? AND wallet IN ({marks})",
        (trade["mint"], trade["side"], since, *watched))
    wallets = {r["wallet"] for r in rows} | {trade["wallet"]}
    if len(wallets) >= config.CLUSTER_MIN_WALLETS:
        return f"cluster_{trade['side']}"
    if trade["side"] == "buy" and trade["quote_symbol"] == "SOL":
        w = watched[trade["wallet"]]
        median = w.get("median_buy_sol")
        if w.get("grade") in config.HIGH_GRADES and median and \
                trade["quote_amount"] >= config.LARGE_BUY_MULTIPLE * median:
            return "large_buy"
    return None


def on_cooldown(mint, kind, now=None):
    now = now or int(time.time())
    rows = db.query("SELECT 1 FROM alerts WHERE mint = ? AND kind = ? AND ts >= ?",
                    (mint, kind, now - config.ALERT_COOLDOWN_MIN * 60))
    return bool(rows)


# --- facts for triage ----------------------------------------------------------------
def gather_facts(mint, kind, watched, now=None):
    now = now or int(time.time())
    market = chain.token_market([mint]).get(mint, {})
    safety = chain.mint_safety(mint)
    launch = market.get("pair_created_ts")
    marks = ",".join("?" * len(watched))
    rows = db.query(
        f"SELECT * FROM trades WHERE mint = ? AND ts >= ? AND wallet IN ({marks}) ORDER BY ts",
        (mint, now - 86400, *watched))
    first_buy = next((r["ts"] for r in rows if r["side"] == "buy"), None)
    graded_in = [{
        "grade": watched[r["wallet"]]["grade"], "wallet": r["wallet"][:6], "side": r["side"],
        "size": f"{r['quote_amount']:.3f} {r['quote_symbol']}",
        "mins_ago": round((now - r["ts"]) / 60, 1),
        "mins_after_launch": round((r["ts"] - launch) / 60, 1) if launch else None,
    } for r in rows]
    return {
        "alert_type": kind,
        "token": market.get("symbol") or mint,
        "mint": mint,
        "pair_age_min": round((now - launch) / 60, 1) if launch else None,
        "liquidity_usd": market.get("liquidity_usd"),
        "fdv_usd": market.get("fdv"),
        "price_usd": market.get("price_usd"),
        "venue": market.get("dex"),
        **safety,
        "graded_wallet_count": len({r["wallet"] for r in rows if r["side"] == "buy"}),
        "mins_since_first_graded_buy": round((now - first_buy) / 60, 1) if first_buy else None,
        "graded_trades_24h": graded_in,
    }


def hard_rules(facts):
    """Deterministic guards. The AI can make a verdict stricter, never looser."""
    reasons = []
    if facts.get("mint_authority_open"):
        reasons.append("mint authority open")
    if facts.get("freeze_authority_open"):
        reasons.append("freeze authority open")
    late = facts.get("mins_since_first_graded_buy")
    if late is not None and late > config.LATE_AFTER_MIN:
        reasons.append(f"late: {late:.0f} min after first graded buy, you'd be exit liquidity")
    return ("no", reasons) if reasons else (None, reasons)


# --- Discord ---------------------------------------------------------------------
def send_discord(title, description, color, fields, mint):
    if not config.DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set; alert: %s %s", title, description)
        return
    embed = {
        "title": title[:256], "description": description[:4000], "color": color,
        "fields": [{"name": k, "value": str(v)[:1024], "inline": True} for k, v in fields if v not in (None, "")],
        "footer": {"text": "Advisor only · small size · you press the button · >20 min late = skip"},
        "url": f"https://dexscreener.com/solana/{mint}",
    }
    links = (f"[DexScreener](https://dexscreener.com/solana/{mint}) · "
             f"[Solscan](https://solscan.io/token/{mint}) · [RugCheck](https://rugcheck.xyz/tokens/{mint})")
    embed["fields"].append({"name": "Links", "value": links, "inline": False})
    r = httpx.post(config.DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()


# --- main entry ----------------------------------------------------------------
def process_tx(tx):
    if not tx.get("signature") or not db.mark_seen(tx["signature"]):
        return []  # duplicate delivery
    watched = db.watched_wallets()
    sent = []
    for trade in parse_swaps(tx, watched):
        db.insert_trade(trade, live=True)
        kind = check_trigger(trade, watched)
        if not kind or on_cooldown(trade["mint"], kind):
            continue
        try:
            sent.append(handle_alert(trade, kind, watched))
        except Exception:
            log.exception("alert failed for %s", trade["mint"])
    return sent


def handle_alert(trade, kind, watched):
    mint = trade["mint"]
    facts = gather_facts(mint, kind, watched)
    symbol = facts["token"] if facts["token"] != mint else mint[:6] + "…"
    w = watched[trade["wallet"]]

    if kind == "cluster_sell":
        # Exits are information, not a trade idea, so skip the AI call.
        n = len({t["wallet"] for t in facts["graded_trades_24h"] if t["side"] == "sell"})
        line = f"{n} graded wallets are selling {symbol}. If you hold it, check your exit."
        send_discord(f"EXIT · {symbol}", line, COLORS["info"],
                     [("Price", facts["price_usd"]), ("Liquidity", facts["liquidity_usd"])], mint)
        db.log_alert(mint, kind, "info", line, facts["price_usd"], facts)
        return {"mint": mint, "kind": kind, "verdict": "info", "line": line}

    forced, reasons = hard_rules(facts)
    try:
        ai = brain.triage(facts)
    except Exception as e:
        log.exception("triage failed")
        ai = {"verdict": "your call", "line": f"AI triage unavailable ({type(e).__name__}); check manually.",
              "red_flags": [], "wallets_read": ""}
    verdict = worst(ai["verdict"], forced)
    flags = list(dict.fromkeys(reasons + ai.get("red_flags", [])))
    line = ai["line"] if not forced else f"{'; '.join(reasons)}. {ai['line']}"

    trigger = (f"{facts['graded_wallet_count']} graded wallets in" if kind == "cluster_buy"
               else f"{w['grade']} wallet bought {trade['quote_amount']:.2f} SOL (usual {w['median_buy_sol']:.2f})")
    send_discord(
        f"{verdict.upper()} · {symbol}", line, COLORS[verdict],
        [("Trigger", trigger),
         ("Wallets", ai.get("wallets_read")),
         ("Red flags", ", ".join(flags) or "none found"),
         ("Pair age", f"{facts['pair_age_min']} min" if facts["pair_age_min"] is not None else "?"),
         ("Liquidity", f"${facts['liquidity_usd']:,.0f}" if facts["liquidity_usd"] else "?"),
         ("Top-10 holders", f"{facts['top10_share']:.0%}" if facts["top10_share"] is not None else "?"),
         ("Mint", f"`{mint}`")],
        mint)
    db.log_alert(mint, kind, verdict, line, facts["price_usd"], {**facts, "ai": ai, "forced": reasons})
    return {"mint": mint, "kind": kind, "verdict": verdict, "line": line}
