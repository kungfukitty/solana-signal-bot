# Solana Signal Bot

An alert-only copy of the Fiction Finance "Eyes / Brain / Nerves" system. It watches a handful of
graded Solana wallets, has Claude triage each interesting trade, and posts a verdict to Discord.
**It never buys or sells.** You press the button, with play money only.

```
Kolscan top 50 ──► grade_wallets.py ──► 5-10 watched wallets ──► setup_webhook.py
   (Eyes)          Helius history,                                    │
                   junk filter, Claude                                ▼
                   grading  (Brain)                   Helius webhook ──► server.py
                                                                         │ trigger rules
                                                                         │ safety checks + Claude triage
                                                                         ▼
                                                                 Discord: looks clean / your call / no
                                                                         │
                                       audit.py (monthly) ◄── alerts + your journal.csv
```

## What each phase does

| Phase | File | What happens |
|---|---|---|
| 1. Shortlist | `grade_wallets.py` | Pulls 30 days of swaps per candidate from Helius. Drops baby wallets (no history before the window), one-hit wonders (fewer than 3 profitable tokens, or >70% of wins from one token) and same-block buyers (pairs that bought the same token in the same slot 3+ times; the better one is kept). |
| 2. Grading | `grade_wallets.py` → `brain.py` | Sends per-token stats (realized PnL, % sold, minutes after launch, venue, hour) to Claude Opus with the video's grading prompt. Saves grade, one-line why, and lucky/insider/bot flag. Picks up to 10 for the watchlist and writes `grades_report.md`. |
| 3. Alerting | `setup_webhook.py`, `server.py` | Registers a Helius webhook for the watchlist. Calls Claude only when 2+ graded wallets trade the same token within 30 min, or an A/A- wallet buys at 2x+ its usual size. Graded-wallet exits get a plain "EXIT" notice. |
| 4. Triage | `nerves.py` → `brain.py` | Checks mint and freeze authority, top-10 holder share, liquidity, pair age, and who is in and how early. Claude (Sonnet, for speed) returns one phone line and a verdict. **Hard rules override the AI**: an open mint or freeze authority, or being >20 min behind the first graded buy, is always "no". |
| 5. Audit | `audit.py` | Monthly: how each verdict's tokens moved since the alert, your journal trades vs the rules (no alert, bought on "no", chased >20 min), and which grades are stale. |

## Setup (about 30 minutes)

1. **Keys.** Get a free [Helius](https://dashboard.helius.dev) API key and an
   [Anthropic API](https://console.anthropic.com) key, and create a Discord channel webhook.
2. **Install.**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env        # fill it in
   ```
3. **Shortlist and grade.** Copy the top 50 wallets from the
   [Kolscan](https://kolscan.io/leaderboard) 30-day leaderboard into `candidates.txt` (see
   `candidates.example.txt`), then:
   ```bash
   python grade_wallets.py candidates.txt
   ```
   This takes a few minutes. Read `grades_report.md` and sanity-check the watchlist. To change it by
   hand: `sqlite3 bot.db "UPDATE wallets SET watch=1 WHERE address='...'"`.
4. **Deploy the server** somewhere with a public HTTPS URL. Railway, Render, Fly.io or a $5 VPS all
   work. The start command is:
   ```bash
   uvicorn server:app --host 0.0.0.0 --port $PORT
   ```
   Put the URL (ending in `/helius`) in `PUBLIC_WEBHOOK_URL`. Give the host a persistent disk for
   `bot.db`, or copy your graded `bot.db` up with the code.
5. **Connect Helius.**
   ```bash
   python setup_webhook.py
   ```
   Open `https://<your-url>/health` to confirm the server sees your watchlist.

## Monthly routine

```bash
python audit.py                               # review last month
python grade_wallets.py candidates.txt        # fresh Kolscan top 50, re-grade
python setup_webhook.py                       # point the webhook at the new watchlist
```

## Rules the code enforces, and the ones it can't

- **No automated buys.** There is no wallet, private key or swap code anywhere in this project. Keep it that way.
- **No chasing.** More than 20 minutes after the first graded buy means a forced "no" verdict.
- **Contract basics.** An open mint or freeze authority means a forced "no".
- **Small sizing** is on you. The audit flags it, but only if you log trades in `journal.csv`.

## Costs

- Helius: 1 credit per webhook event. With 10 wallets that fits the free tier.
- Claude: one Opus call per month for grading. Triage uses Sonnet, only on triggered alerts.
- DexScreener: free, no key.

## Tuning

All thresholds live in `.env` (see `.env.example`). If you get too many alerts, raise
`CLUSTER_MIN_WALLETS` to 3 or `LARGE_BUY_MULTIPLE` to 3. To get a faster, cheaper triage, set
`TRIAGE_MODEL=claude-haiku-4-5-20251001`.

## Limits worth knowing

- PnL counts SOL-quoted swaps only. Tokens bought before the 30-day window aren't scored.
- "Minutes after launch" uses the token's first DexScreener pair. Pump.fun bonding-curve buys
  before migration can show up as negative minutes, and the grading prompt treats very early
  entries as a possible insider or sniper signal.
- Kolscan has no API, so the candidate list is copied in by hand once a month.
- This is a research tool, not financial advice. Most memecoins go to zero.
