"""Turn a Helius *enhanced* transaction into buy/sell records for the wallets we care about.

Works on both live webhook payloads and history fetched from /v0/addresses/{a}/transactions,
because Helius sends the same enhanced shape in both places.
"""
import config

LAMPORTS = 1_000_000_000
MIN_QUOTE = 0.0001  # ignore dust / airdrops with no real payment


def _from_swap_event(tx, wallet):
    """Use Helius' parsed swap event when present (most reliable for DEX swaps)."""
    ev = (tx.get("events") or {}).get("swap")
    if not ev:
        return None
    sol, tokens = 0.0, {}
    ni, no = ev.get("nativeInput") or {}, ev.get("nativeOutput") or {}
    if ni.get("account") == wallet:
        sol -= int(ni.get("amount") or 0) / LAMPORTS
    if no.get("account") == wallet:
        sol += int(no.get("amount") or 0) / LAMPORTS
    for key, sign in (("tokenInputs", -1), ("tokenOutputs", 1)):
        for t in ev.get(key) or []:
            if t.get("userAccount") != wallet:
                continue
            raw = t.get("rawTokenAmount") or {}
            amt = int(raw.get("tokenAmount") or 0) / (10 ** int(raw.get("decimals") or 0))
            tokens[t["mint"]] = tokens.get(t["mint"], 0.0) + sign * amt
    if not sol and not tokens:
        return None
    if config.WSOL_MINT in tokens:  # WSOL counts as SOL
        sol += tokens.pop(config.WSOL_MINT)
    return sol, tokens


def _from_transfers(tx, wallet):
    """Fallback: net the wallet's token transfers and SOL balance change."""
    tokens = {}
    for t in tx.get("tokenTransfers") or []:
        amt = float(t.get("tokenAmount") or 0)
        if t.get("toUserAccount") == wallet:
            tokens[t["mint"]] = tokens.get(t["mint"], 0.0) + amt
        if t.get("fromUserAccount") == wallet:
            tokens[t["mint"]] = tokens.get(t["mint"], 0.0) - amt
    native = 0.0
    for a in tx.get("accountData") or []:
        if a.get("account") == wallet:
            native = int(a.get("nativeBalanceChange") or 0) / LAMPORTS
            if tx.get("feePayer") == wallet:
                native += int(tx.get("fee") or 0) / LAMPORTS  # fees aren't trade size
    wsol = tokens.pop(config.WSOL_MINT, 0.0)
    # Wrapping/unwrapping shows the same SOL twice (native + WSOL) with the same sign:
    # keep the larger one. Opposite signs are genuinely separate flows, so add them.
    if native * wsol > 0:
        sol = native if abs(native) >= abs(wsol) else wsol
    else:
        sol = native + wsol
    return sol, tokens


def parse_swaps(tx, wallets):
    """Return a list of trade dicts for every wallet in `wallets` that swapped in this tx."""
    out = []
    if tx.get("transactionError"):
        return out
    involved = {tx.get("feePayer")}
    for t in tx.get("tokenTransfers") or []:
        involved.update((t.get("fromUserAccount"), t.get("toUserAccount")))
    for wallet in involved & set(wallets):
        parsed = _from_swap_event(tx, wallet) or _from_transfers(tx, wallet)
        sol, tokens = parsed
        usd = tokens.pop(config.USDC_MINT, 0.0) + tokens.pop(config.USDT_MINT, 0.0)
        tokens = {m: d for m, d in tokens.items() if abs(d) > 0}
        if not tokens:
            continue
        mint, delta = max(tokens.items(), key=lambda kv: abs(kv[1]))
        side = "buy" if delta > 0 else "sell"
        if abs(sol) >= MIN_QUOTE and (sol < 0) == (side == "buy"):
            quote, sym = abs(sol), "SOL"
        elif abs(usd) >= 0.01 and (usd < 0) == (side == "buy"):
            quote, sym = abs(usd), "USD"
        else:
            continue  # a transfer or airdrop, not a trade
        out.append({
            "signature": tx["signature"], "wallet": wallet, "mint": mint, "side": side,
            "quote_amount": round(quote, 9), "quote_symbol": sym, "token_amount": abs(delta),
            "slot": tx.get("slot"), "ts": tx.get("timestamp"), "source": tx.get("source") or "UNKNOWN",
        })
    return out
