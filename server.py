"""Webhook receiver. Helius POSTs enhanced transactions here.

    uvicorn server:app --host 0.0.0.0 --port 8000
"""
import hmac
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

import config
import db
import nerves

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="solana-signal-bot")


@app.get("/health")
def health():
    return {"ok": True, "watching": len(db.watched_wallets())}


@app.post("/helius")
async def helius(request: Request, background: BackgroundTasks, authorization: str = Header(default="")):
    if not config.WEBHOOK_AUTH_SECRET or not hmac.compare_digest(authorization, config.WEBHOOK_AUTH_SECRET):
        raise HTTPException(status_code=401)
    payload = await request.json()
    txs = payload if isinstance(payload, list) else [payload]
    # Answer Helius immediately; triage can take a few seconds.
    for tx in txs:
        background.add_task(nerves.process_tx, tx)
    return {"received": len(txs)}
