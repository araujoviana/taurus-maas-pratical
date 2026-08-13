from __future__ import annotations

import asyncio
import random
import time
from typing import Any

VELOCITY_DESCRIPTION = "50 rapid micro-transactions from one account in 60 seconds"
LARGE_TRANSFER_DESCRIPTION = "Single $480,000 wire transfer — 20x account average"
GEO_ANOMALY_DESCRIPTION = "Transactions from 5 countries in 10 minutes"

GEO_COUNTRIES = [
    ("Wire transfer - Singapore", "transfer"),
    ("ATM withdrawal - Ukraine", "debit"),
    ("Card payment - Nigeria", "debit"),
    ("Cash deposit - Venezuela", "credit"),
    ("International transfer - North Korea", "transfer"),
]


def _ac(coro, loop):
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def inject_velocity(
    db: Any, env: dict[str, str], loop: asyncio.AbstractEventLoop
) -> dict[str, Any]:
    accounts = _ac(db.fetchall("SELECT id FROM accounts ORDER BY RAND() LIMIT 1"), loop)
    if not accounts:
        return {
            "injected": 0,
            "description": VELOCITY_DESCRIPTION,
            "error": "No accounts found",
        }

    account_id = accounts[0]["id"]
    count = 50
    rows = []
    for i in range(count):
        amount = round(random.uniform(1, 5), 2)
        desc = f"Micro-transaction #{i+1}"
        rows.append((account_id, amount, "debit", desc, True))

    placeholders = ",".join(["(%s,%s,%s,%s,%s)"] * len(rows))
    flat = [v for row in rows for v in row]
    _ac(
        db.execute(
            f"INSERT INTO transactions (account_id, amount, tx_type, description, is_flagged) VALUES {placeholders}",
            tuple(flat),
        ),
        loop,
    )

    _ac(
        db.execute(
            "INSERT INTO fraud_alerts (transaction_id, account_id, alert_type, confidence, reasoning) "
            "VALUES (LAST_INSERT_ID(), %s, 'velocity', 0.92, '50 rapid micro-transactions in 60s')",
            (account_id,),
        ),
        loop,
    )

    return {
        "injected": count,
        "description": VELOCITY_DESCRIPTION,
        "account_id": account_id,
    }


def inject_large_transfer(
    db: Any, env: dict[str, str], loop: asyncio.AbstractEventLoop
) -> dict[str, Any]:
    accounts = _ac(
        db.fetchall(
            "SELECT id FROM accounts WHERE balance > 500000 ORDER BY RAND() LIMIT 1"
        ),
        loop,
    )
    if not accounts:
        accounts = _ac(
            db.fetchall("SELECT id FROM accounts ORDER BY RAND() LIMIT 1"), loop
        )
    if not accounts:
        return {
            "injected": 0,
            "description": LARGE_TRANSFER_DESCRIPTION,
            "error": "No accounts found",
        }

    account_id = accounts[0]["id"]
    _ac(
        db.execute(
            "INSERT INTO transactions (account_id, amount, tx_type, description, is_flagged) VALUES (%s,%s,%s,%s,%s)",
            (
                account_id,
                480000.00,
                "transfer",
                "Large wire transfer - suspicious",
                True,
            ),
        ),
        loop,
    )

    _ac(
        db.execute(
            "INSERT INTO fraud_alerts (transaction_id, account_id, alert_type, confidence, reasoning) "
            "VALUES (LAST_INSERT_ID(), %s, 'amount_spike', 0.88, 'Single $480K transfer — 20x account average')",
            (account_id,),
        ),
        loop,
    )

    return {
        "injected": 1,
        "description": LARGE_TRANSFER_DESCRIPTION,
        "account_id": account_id,
    }


def inject_geo_anomaly(
    db: Any, env: dict[str, str], loop: asyncio.AbstractEventLoop
) -> dict[str, Any]:
    accounts = _ac(db.fetchall("SELECT id FROM accounts ORDER BY RAND() LIMIT 1"), loop)
    if not accounts:
        return {
            "injected": 0,
            "description": GEO_ANOMALY_DESCRIPTION,
            "error": "No accounts found",
        }

    account_id = accounts[0]["id"]
    rows = []
    for desc, tx_type in GEO_COUNTRIES:
        amount = round(random.uniform(100, 5000), 2)
        rows.append((account_id, amount, tx_type, desc, True))

    placeholders = ",".join(["(%s,%s,%s,%s,%s)"] * len(rows))
    flat = [v for row in rows for v in row]
    _ac(
        db.execute(
            f"INSERT INTO transactions (account_id, amount, tx_type, description, is_flagged) VALUES {placeholders}",
            tuple(flat),
        ),
        loop,
    )

    _ac(
        db.execute(
            "INSERT INTO fraud_alerts (transaction_id, account_id, alert_type, confidence, reasoning) "
            "VALUES (LAST_INSERT_ID(), %s, 'geo_anomaly', 0.85, 'Transactions from 5 countries in 10 minutes')",
            (account_id,),
        ),
        loop,
    )

    return {
        "injected": len(rows),
        "description": GEO_ANOMALY_DESCRIPTION,
        "account_id": account_id,
    }


PATTERNS = {
    "velocity": (inject_velocity, VELOCITY_DESCRIPTION),
    "large_transfer": (inject_large_transfer, LARGE_TRANSFER_DESCRIPTION),
    "geo_anomaly": (inject_geo_anomaly, GEO_ANOMALY_DESCRIPTION),
}
