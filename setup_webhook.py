"""Point a Helius webhook at this service for every wallet on the watchlist.

    python setup_webhook.py

Re-run after every re-grade; it updates the existing webhook instead of creating a new one.
"""
import sys

import chain
import config
import db

if not (config.HELIUS_API_KEY and config.PUBLIC_WEBHOOK_URL and config.WEBHOOK_AUTH_SECRET):
    sys.exit("Set HELIUS_API_KEY, PUBLIC_WEBHOOK_URL and WEBHOOK_AUTH_SECRET in .env first.")

watch = db.watched_wallets()
if not watch:
    sys.exit("Watchlist is empty. Run grade_wallets.py first.")

hook = chain.upsert_webhook(watch.keys())
print(f"Webhook {hook.get('webhookID')} now watches {len(watch)} wallets -> {config.PUBLIC_WEBHOOK_URL}")
for a, w in watch.items():
    print(f"  {w['grade'] or '?':>2}  {a}  {w['label'] or ''}")
