"""Offline tests: no network. Helius, DexScreener, Claude and Discord are all mocked."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["WEBHOOK_AUTH_SECRET"] = "s3cret"

import config  # noqa: E402
import db  # noqa: E402
import grade_wallets  # noqa: E402
import nerves  # noqa: E402
from swaps import parse_swaps  # noqa: E402

A, B, C = "WalletAAAA1111", "WalletBBBB2222", "WalletCCCC3333"
MINT = "MemeMint9999pump"
POOL = "PoolXXXX"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "WEBHOOK_AUTH_SECRET", "s3cret")
    db._conn = None
    yield
    db._conn = None


def swap_event_tx(sig, wallet, sol, tokens, ts=None, side="buy"):
    """Helius enhanced tx with a parsed swap event."""
    ev = {"nativeInput": None, "nativeOutput": None, "tokenInputs": [], "tokenOutputs": []}
    lam = str(int(sol * 1e9))
    raw = {"tokenAmount": str(int(tokens * 1e6)), "decimals": 6}
    if side == "buy":
        ev["nativeInput"] = {"account": wallet, "amount": lam}
        ev["tokenOutputs"] = [{"userAccount": wallet, "mint": MINT, "rawTokenAmount": raw}]
    else:
        ev["nativeOutput"] = {"account": wallet, "amount": lam}
        ev["tokenInputs"] = [{"userAccount": wallet, "mint": MINT, "rawTokenAmount": raw}]
    return {"signature": sig, "feePayer": wallet, "timestamp": ts or int(time.time()), "slot": 1,
            "source": "PUMP_FUN", "type": "SWAP", "fee": 5000, "events": {"swap": ev},
            "tokenTransfers": [{"fromUserAccount": POOL, "toUserAccount": wallet, "mint": MINT,
                                "tokenAmount": tokens}], "accountData": []}


# --- parsing ---------------------------------------------------------------------
def test_parse_swap_event_buy():
    t = parse_swaps(swap_event_tx("s1", A, 0.5, 1000), {A})
    assert len(t) == 1 and t[0]["side"] == "buy" and t[0]["mint"] == MINT
    assert t[0]["quote_amount"] == 0.5 and t[0]["quote_symbol"] == "SOL"


def test_parse_fallback_with_wsol_wrap_not_double_counted():
    tx = {"signature": "s2", "feePayer": A, "timestamp": 1, "slot": 1, "source": "RAYDIUM", "fee": 5000,
          "accountData": [{"account": A, "nativeBalanceChange": -1_002_044_280}],  # 1 SOL + rent - fee
          "tokenTransfers": [
              {"fromUserAccount": A, "toUserAccount": POOL, "mint": config.WSOL_MINT, "tokenAmount": 1.0},
              {"fromUserAccount": POOL, "toUserAccount": A, "mint": MINT, "tokenAmount": 5000}]}
    t = parse_swaps(tx, {A})
    assert t[0]["side"] == "buy" and abs(t[0]["quote_amount"] - 1.002) < 0.01


def test_parse_ignores_airdrop():
    tx = {"signature": "s3", "feePayer": POOL, "timestamp": 1, "slot": 1, "accountData": [],
          "tokenTransfers": [{"fromUserAccount": POOL, "toUserAccount": A, "mint": MINT, "tokenAmount": 9}]}
    assert parse_swaps(tx, {A}) == []


def test_parse_sell_for_usdc():
    tx = {"signature": "s4", "feePayer": A, "timestamp": 1, "slot": 1, "accountData": [],
          "tokenTransfers": [
              {"fromUserAccount": A, "toUserAccount": POOL, "mint": MINT, "tokenAmount": 10},
              {"fromUserAccount": POOL, "toUserAccount": A, "mint": config.USDC_MINT, "tokenAmount": 25.0}]}
    t = parse_swaps(tx, {A})
    assert t[0]["side"] == "sell" and t[0]["quote_symbol"] == "USD" and t[0]["quote_amount"] == 25.0


# --- grading stats & junk filter ----------------------------------------------------
def test_stats_and_one_hit_and_same_block():
    now = int(time.time())
    trades = [
        {"mint": "T1", "side": "buy", "quote_symbol": "SOL", "quote_amount": 1, "token_amount": 100, "ts": now, "slot": 5, "source": "X"},
        {"mint": "T1", "side": "sell", "quote_symbol": "SOL", "quote_amount": 3, "token_amount": 50, "ts": now + 60, "slot": 6, "source": "X"},
        {"mint": "T2", "side": "buy", "quote_symbol": "SOL", "quote_amount": 1, "token_amount": 10, "ts": now, "slot": 9, "source": "X"},
    ]
    rows = grade_wallets.per_token_stats(trades, {"T1": {"pair_created_ts": now - 300, "symbol": "ONE"}})
    t1 = next(r for r in rows if r["mint"] == "T1")
    assert t1["sold_pct"] == 0.5 and t1["realized_pnl_sol"] == 2.5 and t1["mins_after_launch"] == 5.0
    s = grade_wallets.wallet_summary(A, rows)
    assert s["profitable_tokens"] == 1 and s["top_token_share_of_wins"] == 1.0
    shared = [{"mint": f"M{i}", "first_buy_slot": i} for i in range(3)]
    assert grade_wallets.same_block_clusters({A: shared, B: shared, C: shared[:1]}) == [(A, B, 3)]


# --- triggers & hard rules -------------------------------------------------------------
def setup_watch():
    db.upsert_wallet(A, grade="A", watch=1, median_buy_sol=0.5, label="alpha")
    db.upsert_wallet(B, grade="B", watch=1, median_buy_sol=1.0)
    db.upsert_wallet(C, grade="C", watch=0)
    return db.watched_wallets()


def trade(wallet, sol, side="buy", ts=None, sig=None):
    return {"signature": sig or f"{wallet}{sol}{side}", "wallet": wallet, "mint": MINT, "side": side,
            "quote_amount": sol, "quote_symbol": "SOL", "token_amount": 1, "slot": 1,
            "ts": ts or int(time.time()), "source": "X"}


def test_single_normal_buy_does_not_trigger():
    w = setup_watch()
    t = trade(B, 1.0)
    db.insert_trade(t)
    assert nerves.check_trigger(t, w) is None


def test_cluster_and_large_buy_triggers():
    w = setup_watch()
    db.insert_trade(trade(B, 1.0))
    t = trade(A, 0.5)
    assert nerves.check_trigger(t, w) == "cluster_buy"
    db._conn = None
    config.DB_PATH = config.DB_PATH + "2"
    w = setup_watch()
    assert nerves.check_trigger(trade(A, 1.5), w) == "large_buy"   # 3x an A wallet's median
    assert nerves.check_trigger(trade(B, 5.0), w) is None          # B isn't high-grade


def test_hard_rules_override():
    assert nerves.hard_rules({"mint_authority_open": True})[0] == "no"
    assert nerves.hard_rules({"mins_since_first_graded_buy": 25})[0] == "no"
    assert nerves.hard_rules({"mins_since_first_graded_buy": 5})[0] is None
    assert nerves.worst("looks clean", "no") == "no" and nerves.worst("your call", None) == "your call"


# --- end to end through the HTTP server ----------------------------------------------------
@pytest.fixture
def mocks(monkeypatch):
    sent = []
    monkeypatch.setattr(nerves.chain, "token_market", lambda m: {MINT: {
        "symbol": "MEME", "pair_created_ts": int(time.time()) - 600, "price_usd": 0.001,
        "liquidity_usd": 40000, "fdv": 900000, "dex": "pumpswap"}})
    state = {"mint_open": False}
    monkeypatch.setattr(nerves.chain, "mint_safety", lambda m: {
        "mint_authority_open": state["mint_open"], "freeze_authority_open": False,
        "is_token_2022": False, "top10_share": 0.22})
    monkeypatch.setattr(nerves.brain, "triage", lambda f: {
        "red_flags": [], "wallets_read": f"{f['graded_wallet_count']} graded, early",
        "line": "2 graded wallets early, authorities revoked.", "verdict": "looks clean"})
    monkeypatch.setattr(nerves, "send_discord", lambda *a: sent.append(a))
    return sent, state


def post(client, tx, auth="s3cret"):
    return client.post("/helius", json=[tx], headers={"Authorization": auth})


def test_end_to_end(mocks):
    from fastapi.testclient import TestClient
    import server
    sent, state = mocks
    setup_watch()
    client = TestClient(server.app)

    assert post(client, swap_event_tx("e1", B, 1.0, 100), auth="wrong").status_code == 401
    assert post(client, swap_event_tx("e1", B, 1.0, 100)).status_code == 200
    assert sent == []                                     # one normal buy: no alert
    post(client, swap_event_tx("e1", B, 1.0, 100))        # duplicate delivery ignored
    post(client, swap_event_tx("e2", A, 0.5, 100))        # second graded wallet -> cluster
    assert len(sent) == 1 and sent[0][0].startswith("LOOKS CLEAN")
    alerts = db.query("SELECT * FROM alerts")
    assert alerts[0]["verdict"] == "looks clean" and alerts[0]["kind"] == "cluster_buy"

    # cooldown: a third buy doesn't re-alert the same token
    db.upsert_wallet(C, grade="A", watch=1, median_buy_sol=1)
    post(client, swap_event_tx("e3", C, 0.2, 10))
    assert len(sent) == 1

    # mint authority open -> forced NO even though the AI said clean
    db.execute("DELETE FROM alerts")
    state["mint_open"] = True
    post(client, swap_event_tx("e4", C, 0.3, 10))
    assert sent[-1][0].startswith("NO") and "mint authority open" in sent[-1][1]

    # exits
    post(client, swap_event_tx("e5", A, 0.9, 100, side="sell"))
    post(client, swap_event_tx("e6", B, 1.9, 100, side="sell"))
    assert sent[-1][0].startswith("EXIT")


def test_late_alert_is_no(mocks):
    sent, _ = mocks
    setup_watch()
    old = int(time.time()) - 25 * 60
    db.insert_trade(trade(B, 1.0, ts=old), live=True)     # first graded buy 25 min ago
    nerves.process_tx(swap_event_tx("late1", A, 0.5, 100))
    assert sent and sent[-1][0].startswith("NO") and "exit liquidity" in sent[-1][1]
