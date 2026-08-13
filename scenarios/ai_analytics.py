from __future__ import annotations

import asyncio
import json
from typing import Any

from openai import OpenAI


def _ac(coro, loop):
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


ANALYSIS_PROMPT = """You are a senior fraud analyst at a fintech company. Analyze the following transaction data and provide:

1. **Risk Summary**: Overall risk assessment of the transaction pool
2. **Flagged Patterns**: Identify suspicious patterns (unusual amounts, rapid transfers, round numbers)
3. **Recommendations**: Specific actions to take

Transaction sample (most recent flagged + random sample):
{transactions}

Database stats:
- Total accounts: {total_accounts}
- Total transactions: {total_transactions}
- Flagged transactions: {flagged_count}

Provide your analysis in a clear, structured format."""


def run_ai_analysis(
    db: Any,
    api_key: str,
    base_url: str,
    model: str,
    loop: asyncio.AbstractEventLoop,
) -> dict[str, Any]:
    flagged = _ac(
        db.fetchall(
            "SELECT t.id, t.account_id, t.amount, t.tx_type, t.description, t.is_flagged, "
            "a.name, a.email, a.risk_score "
            "FROM transactions t JOIN accounts a ON t.account_id = a.id "
            "WHERE t.is_flagged = TRUE ORDER BY t.id DESC LIMIT 20",
            (),
        ),
        loop,
    )

    sample = _ac(
        db.fetchall(
            "SELECT t.id, t.account_id, t.amount, t.tx_type, t.description, t.is_flagged, "
            "a.name, a.risk_score "
            "FROM transactions t JOIN accounts a ON t.account_id = a.id "
            "ORDER BY RAND() LIMIT 30",
            (),
        ),
        loop,
    )

    total_accounts = _ac(db.fetchone("SELECT COUNT(*) as c FROM accounts"), loop)
    total_tx = _ac(db.fetchone("SELECT COUNT(*) as c FROM transactions"), loop)
    flagged_count = _ac(
        db.fetchone("SELECT COUNT(*) as c FROM transactions WHERE is_flagged = TRUE"),
        loop,
    )

    all_tx = flagged + sample
    tx_text = json.dumps(all_tx, indent=2, default=str)

    prompt = ANALYSIS_PROMPT.format(
        transactions=tx_text,
        total_accounts=total_accounts["c"] if total_accounts else 0,
        total_transactions=total_tx["c"] if total_tx else 0,
        flagged_count=flagged_count["c"] if flagged_count else 0,
    )

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a fraud analysis AI for a fintech platform.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=1500,
        temperature=0.3,
    )

    analysis = response.choices[0].message.content

    high_risk_ids = [r["id"] for r in flagged if r.get("risk_score", 0) >= 7]
    if high_risk_ids:
        placeholders = ",".join(["%s"] * len(high_risk_ids))
        _ac(
            db.execute(
                f"UPDATE accounts SET risk_score = 10 WHERE id IN ({placeholders})",
                tuple(high_risk_ids),
            ),
            loop,
        )

    return {
        "analysis": analysis,
        "flagged_count": flagged_count["c"] if flagged_count else 0,
        "high_risk_accounts": len(high_risk_ids),
        "model": model,
        "tokens_used": response.usage.total_tokens if response.usage else 0,
    }
