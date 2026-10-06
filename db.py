"""SQLite storage: graded wallets, their swaps, and every alert sent (for the monthly audit)."""
import json
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    label TEXT,
    grade TEXT,
    reason TEXT,
    flag TEXT,                 -- lucky / insider / bot / none
    watch INTEGER DEFAULT 0,   -- 1 = on the live watchlist
    excluded_reason TEXT,      -- set by the junk filter
    median_buy_sol REAL,
    stats_json TEXT,
    graded_at INTEGER
);
CREATE TABLE IF NOT EXISTS trades (
    signature TEXT,
    wallet TEXT,
    mint TEXT,
    side TEXT,                 -- buy / sell
    quote_amount REAL,         -- SOL (or USD if quote_symbol = USD)
    quote_symbol TEXT,
    token_amount REAL,
    slot INTEGER,
    ts INTEGER,
    source TEXT,
    live INTEGER DEFAULT 0,    -- 1 = arrived via webhook
    PRIMARY KEY (signature, wallet, mint)
);
CREATE INDEX IF NOT EXISTS trades_mint_ts ON trades(mint, ts);
CREATE INDEX IF NOT EXISTS trades_wallet ON trades(wallet);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT,
    ts INTEGER,
    kind TEXT,                 -- cluster_buy / large_buy / cluster_sell
    verdict TEXT,              -- looks clean / your call / no
    line TEXT,
    price_usd REAL,
    details_json TEXT
);
CREATE TABLE IF NOT EXISTS seen (signature TEXT PRIMARY KEY, ts INTEGER);
"""


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def execute(sql, params=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


# --- helpers ----------------------------------------------------------------
def mark_seen(signature):
    """Returns True the first time a signature is seen (Helius may retry/duplicate)."""
    cur = execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (signature, int(time.time())))
    return cur.rowcount == 1


def upsert_wallet(address, **fields):
    execute("INSERT OR IGNORE INTO wallets(address) VALUES (?)", (address,))
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        execute(f"UPDATE wallets SET {cols} WHERE address = ?", (*fields.values(), address))


def insert_trade(t, live=False):
    execute(
        "INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (t["signature"], t["wallet"], t["mint"], t["side"], t["quote_amount"], t["quote_symbol"],
         t["token_amount"], t["slot"], t["ts"], t["source"], int(live)),
    )


def watched_wallets():
    return {r["address"]: r for r in query("SELECT * FROM wallets WHERE watch = 1")}


def log_alert(mint, kind, verdict, line, price_usd, details):
    execute(
        "INSERT INTO alerts(mint, ts, kind, verdict, line, price_usd, details_json) VALUES (?,?,?,?,?,?,?)",
        (mint, int(time.time()), kind, verdict, line, price_usd, json.dumps(details, default=str)),
    )
