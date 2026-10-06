"""Central settings. Everything is read from environment variables (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


# --- Keys -------------------------------------------------------------------
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# Shared secret Helius sends back in the Authorization header of every webhook call.
WEBHOOK_AUTH_SECRET = os.getenv("WEBHOOK_AUTH_SECRET", "")
# Public URL of this service, e.g. https://my-bot.up.railway.app/helius
PUBLIC_WEBHOOK_URL = os.getenv("PUBLIC_WEBHOOK_URL", "")

# --- Endpoints --------------------------------------------------------------
HELIUS_API_BASE = os.getenv("HELIUS_API_BASE", "https://api-mainnet.helius-rpc.com")
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com")
DEXSCREENER_BASE = "https://api.dexscreener.com"

# --- Models -----------------------------------------------------------------
GRADING_MODEL = os.getenv("GRADING_MODEL", "claude-opus-5-5")   # deep, once a month
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "claude-sonnet-5-5")   # fast, every alert

# --- Storage ----------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "bot.db")

# --- Phase 1/2: shortlist & grading ----------------------------------------
LOOKBACK_DAYS = _i("LOOKBACK_DAYS", 30)            # video: 7 too short, 90 too long
MAX_HISTORY_PAGES = _i("MAX_HISTORY_PAGES", 10)    # 100 swaps per page
ONE_HIT_SHARE = _f("ONE_HIT_SHARE", 0.70)          # >70% of profit from one token = one-hit wonder
MIN_PROFITABLE_TOKENS = _i("MIN_PROFITABLE_TOKENS", 3)
SAME_BLOCK_MIN_SHARED = _i("SAME_BLOCK_MIN_SHARED", 3)  # shared same-slot buys to call it a cluster
WATCHLIST_MAX = _i("WATCHLIST_MAX", 10)
REGRADE_AFTER_DAYS = _i("REGRADE_AFTER_DAYS", 30)

# --- Phase 3/4: triggers & triage ------------------------------------------
CLUSTER_WINDOW_MIN = _i("CLUSTER_WINDOW_MIN", 30)   # 2+ graded wallets on same token in this window
CLUSTER_MIN_WALLETS = _i("CLUSTER_MIN_WALLETS", 2)
LARGE_BUY_MULTIPLE = _f("LARGE_BUY_MULTIPLE", 2.0)  # high-grade buy > 2x its median size
HIGH_GRADES = tuple(os.getenv("HIGH_GRADES", "A,A-").split(","))
LATE_AFTER_MIN = _i("LATE_AFTER_MIN", 20)           # video rule: >20 min late = exit liquidity
ALERT_COOLDOWN_MIN = _i("ALERT_COOLDOWN_MIN", 60)   # don't re-alert the same token too often

# Quote assets: what a "buy" is paid with.
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = {WSOL_MINT, USDC_MINT, USDT_MINT}
