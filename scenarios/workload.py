from __future__ import annotations

import random
import time
from typing import Any, Callable

from faker import Faker


BATCH_SIZE = 500
ACCOUNT_COUNT = 200
TX_PER_ACCOUNT = 50
TOTAL_BATCHES = (ACCOUNT_COUNT + ACCOUNT_COUNT * TX_PER_ACCOUNT) // BATCH_SIZE + 1


def _seed_accounts(fake: Faker, n: int) -> list[tuple]:
    rows = []
    for _ in range(n):
        name = fake.name()
        email = fake.unique.email()
        balance = round(random.uniform(100, 500_000), 2)
        risk = random.choices(range(0, 10), weights=[40, 25, 15, 8, 5, 3, 2, 1, 1, 0])[0]
        rows.append((name, email, balance, risk))
    return rows


def _seed_transactions(account_ids: list[int], n_per_acct: int) -> list[tuple]:
    rows = []
    tx_types = ["credit", "debit", "transfer"]
    descriptions = [
        "ATM withdrawal", "Wire transfer", "Salary deposit",
        "Card payment", "Loan repayment", "Interest credit",
        "Bill payment", "Refund", "Cash deposit", "International transfer",
    ]
    for aid in account_ids:
        for _ in range(n_per_acct):
            amount = round(random.uniform(5, 25_000), 2)
            tx_type = random.choice(tx_types)
            desc = random.choice(descriptions)
            flagged = random.random() < 0.02
            rows.append((aid, amount, tx_type, desc, flagged))
    return rows


def run_workload(
    db: Any,
    env: dict[str, str],
    on_progress: Callable[[float], None] | None = None,
) -> None:
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    fake = Faker()
    Faker.seed(42)
    random.seed(42)

    if on_progress:
        on_progress(5.0)

    account_rows = _seed_accounts(fake, ACCOUNT_COUNT)

    inserted = 0
    batch_num = 0
    for i in range(0, len(account_rows), BATCH_SIZE):
        chunk = account_rows[i : i + BATCH_SIZE]
        placeholders = ",".join(["(%s,%s,%s,%s)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        loop.run_until_complete(
            db.execute(
                f"INSERT INTO accounts (name, email, balance, risk_score) VALUES {placeholders}",
                tuple(flat),
            )
        )
        inserted += len(chunk)
        batch_num += 1
        if on_progress:
            on_progress(round(5 + 30 * batch_num / TOTAL_BATCHES, 1))

    result = loop.run_until_complete(
        db.fetchall("SELECT id FROM accounts ORDER BY id", ())
    )
    account_ids = [r["id"] for r in result]

    tx_rows = _seed_transactions(account_ids, TX_PER_ACCOUNT)
    if on_progress:
        on_progress(40.0)

    tx_inserted = 0
    for i in range(0, len(tx_rows), BATCH_SIZE):
        chunk = tx_rows[i : i + BATCH_SIZE]
        placeholders = ",".join(["(%s,%s,%s,%s,%s)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        loop.run_until_complete(
            db.execute(
                f"INSERT INTO transactions (account_id, amount, tx_type, description, is_flagged) VALUES {placeholders}",
                tuple(flat),
            )
        )
        tx_inserted += len(chunk)
        batch_num += 1
        if on_progress:
            on_progress(round(40 + 55 * (i + BATCH_SIZE) / len(tx_rows), 1))

    if on_progress:
        on_progress(100.0)
