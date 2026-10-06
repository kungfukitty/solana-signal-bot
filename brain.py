"""The Brain: the two Claude prompts from the video, with structured (JSON) answers."""
import json

import config

_client = None


def client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so tests and tooling run without the SDK installed
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _ask(model, system, user, tool, max_tokens):
    """Force Claude to answer through one tool so we always get valid JSON back."""
    msg = client().messages.create(
        model=model, max_tokens=max_tokens, system=system,
        tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude returned no structured answer")


# --- 1. Grading prompt (monthly) ----------------------------------------------
GRADING_SYSTEM = (
    "You're grading crypto wallets on skill, not on results. For each wallet: how many different "
    "tokens did the profit come from? How early did they usually buy relative to launch? Did they "
    "actually sell, or is the profit unrealized? Do the wins share a pattern, like the same launchpad "
    "or the same time of day? Is there any sign the wallet is the token's own team? Give each wallet a "
    "grade, one sentence of why, and a flag if it looks lucky, like an insider, or like a bot. Name three "
    "you'd alert me on and three you'd never follow, with reasons.\n\n"
    "Grades: A, A-, B, C, D, F. Reserve A/A- for repeatable skill across many tokens with realized "
    "profit. The data is computed from on-chain swaps over the last 30 days; 'mins_after_launch' is "
    "minutes between the token's first DEX pair and the wallet's first buy (negative or ~0 can mean "
    "insider/sniper bot). Be skeptical: a big PnL from one token is luck until proven otherwise."
)

GRADING_TOOL = {
    "name": "submit_grades",
    "description": "Submit a grade for every wallet plus the follow / never-follow picks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "wallets": {"type": "array", "items": {"type": "object", "properties": {
                "address": {"type": "string"},
                "grade": {"type": "string", "enum": ["A", "A-", "B", "C", "D", "F"]},
                "why": {"type": "string", "description": "One sentence."},
                "flag": {"type": "string", "enum": ["none", "lucky", "insider", "bot"]},
            }, "required": ["address", "grade", "why", "flag"]}},
            "follow": {"type": "array", "items": {"type": "object", "properties": {
                "address": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["address", "reason"]}},
            "never": {"type": "array", "items": {"type": "object", "properties": {
                "address": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["address", "reason"]}},
        },
        "required": ["wallets", "follow", "never"],
    },
}


def grade_wallets(summaries):
    user = "Here are the wallets and their 30-day trade histories:\n\n" + json.dumps(summaries, indent=1)
    return _ask(config.GRADING_MODEL, GRADING_SYSTEM, user, GRADING_TOOL, max_tokens=16000)


# --- 2. Triage prompt (every alert, ~10 seconds) --------------------------------
TRIAGE_SYSTEM = (
    "You are a bouncer at the door for crypto trade alerts on Solana. You get on-chain facts about a "
    "token my graded wallets just traded. First, any obvious red flags (mint authority still open, "
    "freeze authority still open, very concentrated holders, thin liquidity, brand-new pair)? Second, "
    "how many of my graded wallets are in, and are they early or late? Third, give me one line I can "
    "read on my phone and a verdict that is only ever 'looks clean', 'your call', or 'no'. "
    "You are an advisor only; never tell me to buy. When unsure, say 'your call' or 'no'."
)

TRIAGE_TOOL = {
    "name": "submit_triage",
    "description": "Submit the triage read.",
    "input_schema": {
        "type": "object",
        "properties": {
            "red_flags": {"type": "array", "items": {"type": "string"}},
            "wallets_read": {"type": "string", "description": "How many graded wallets, early or late."},
            "line": {"type": "string", "description": "One phone-readable line, under 140 characters."},
            "verdict": {"type": "string", "enum": ["looks clean", "your call", "no"]},
        },
        "required": ["red_flags", "wallets_read", "line", "verdict"],
    },
}


def triage(facts):
    user = "Alert facts:\n" + json.dumps(facts, indent=1, default=str)
    return _ask(config.TRIAGE_MODEL, TRIAGE_SYSTEM, user, TRIAGE_TOOL, max_tokens=1000)
