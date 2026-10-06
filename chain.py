"""External data: Helius (history, RPC, webhooks) and DexScreener (price, launch time, liquidity)."""
import time

import httpx

import config

_http = httpx.Client(timeout=20)


def _get(url, params=None, tries=3):
    for i in range(tries):
        r = _http.get(url, params=params)
        if r.status_code == 429 and i < tries - 1:
            time.sleep(2 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()


def _rpc(method, params):
    r = _http.post(config.HELIUS_RPC_URL, params={"api-key": config.HELIUS_API_KEY},
                   json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


# --- Helius: wallet history --------------------------------------------------
def wallet_history(address, since_ts, max_pages=config.MAX_HISTORY_PAGES):
    """Enhanced transactions for `address` newer than since_ts (newest first)."""
    url = f"{config.HELIUS_API_BASE}/v0/addresses/{address}/transactions"
    params = {"api-key": config.HELIUS_API_KEY, "limit": 100, "gte-time": since_ts,
              "token-accounts": "balanceChanged"}
    txs = []
    for _ in range(max_pages):
        page = _get(url, params)
        if not page:
            break
        txs.extend(page)
        params["before-signature"] = page[-1]["signature"]
        time.sleep(0.2)
    return txs


def has_activity_before(address, ts):
    """False = 'baby wallet': nothing on record before the lookback window."""
    url = f"{config.HELIUS_API_BASE}/v0/addresses/{address}/transactions"
    page = _get(url, {"api-key": config.HELIUS_API_KEY, "limit": 1, "lt-time": ts})
    return bool(page)


# --- Helius RPC: contract safety ----------------------------------------------
def mint_safety(mint):
    """Mint/freeze authority, supply and top-10 holder share."""
    info = _rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    parsed = ((info or {}).get("value") or {}).get("data", {}).get("parsed", {}).get("info", {})
    out = {
        "mint_authority_open": parsed.get("mintAuthority") is not None,
        "freeze_authority_open": parsed.get("freezeAuthority") is not None,
        "is_token_2022": ((info or {}).get("value") or {}).get("owner", "").startswith("TokenzQd"),
        "top10_share": None,
    }
    try:
        supply = float(_rpc("getTokenSupply", [mint])["value"]["uiAmount"] or 0)
        largest = _rpc("getTokenLargestAccounts", [mint])["value"]
        if supply:
            out["top10_share"] = round(sum(float(a["uiAmount"] or 0) for a in largest[:10]) / supply, 3)
    except Exception:
        pass  # some tokens error on these calls; triage still runs with what we have
    return out


# --- DexScreener: market data -------------------------------------------------
def token_market(mints):
    """{mint: {price_usd, liquidity_usd, fdv, pair_created_ts, dex, symbol}} for up to N mints."""
    out = {}
    mints = list(dict.fromkeys(mints))
    for i in range(0, len(mints), 30):  # endpoint accepts 30 addresses per call
        chunk = ",".join(mints[i:i + 30])
        try:
            pairs = _get(f"{config.DEXSCREENER_BASE}/tokens/v1/solana/{chunk}") or []
        except httpx.HTTPError:
            continue
        for p in pairs:
            m = p.get("baseToken", {}).get("address")
            if not m:
                continue
            created = (p.get("pairCreatedAt") or 0) // 1000 or None
            liq = (p.get("liquidity") or {}).get("usd") or 0
            cur = out.get(m)
            if cur is None:
                out[m] = cur = {"symbol": p["baseToken"].get("symbol"), "pair_created_ts": created,
                                "price_usd": None, "liquidity_usd": 0, "fdv": None, "dex": None}
            # earliest pair = launch time; deepest pair = price
            if created and (not cur["pair_created_ts"] or created < cur["pair_created_ts"]):
                cur["pair_created_ts"] = created
            if liq >= cur["liquidity_usd"]:
                cur.update(price_usd=float(p.get("priceUsd") or 0) or None, liquidity_usd=liq,
                           fdv=p.get("fdv"), dex=p.get("dexId"))
        time.sleep(0.25)
    return out


# --- Helius: webhook management -----------------------------------------------
def upsert_webhook(addresses):
    """Create the webhook, or update the existing one pointing at our URL."""
    base = f"{config.HELIUS_API_BASE}/v0/webhooks"
    key = {"api-key": config.HELIUS_API_KEY}
    body = {"webhookURL": config.PUBLIC_WEBHOOK_URL, "transactionTypes": ["ANY"],
            "accountAddresses": list(addresses), "webhookType": "enhanced",
            "authHeader": config.WEBHOOK_AUTH_SECRET}
    existing = [w for w in _get(base, key) or [] if w.get("webhookURL") == config.PUBLIC_WEBHOOK_URL]
    if existing:
        r = _http.put(f"{base}/{existing[0]['webhookID']}", params=key, json=body)
    else:
        r = _http.post(base, params=key, json=body)
    r.raise_for_status()
    return r.json()
