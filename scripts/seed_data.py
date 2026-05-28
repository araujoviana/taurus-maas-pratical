"""
seed_data.py — Bulk-seeds the fintech demo database with accounts and transactions.
Run once after `make up`.
"""

import random
from pathlib import Path

import pymysql
from dotenv import dotenv_values
from faker import Faker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

ACCOUNTS_COUNT = 10_000
TRANSACTIONS_COUNT = 500_000
BATCH_SIZE = 1000

RISK_SCORE_WEIGHTS = [40, 25, 15, 8, 5, 3, 2, 1, 1, 0]  # index == risk_score
RISK_SCORES = list(range(10))

TX_TYPES = ["credit", "debit", "transfer"]

DESCRIPTIONS = [
    "Online purchase",
    "ATM withdrawal",
    "Wire transfer",
    "Bill payment",
    "Salary deposit",
    "Refund received",
    "Subscription fee",
    "Peer-to-peer transfer",
    "Foreign exchange",
    "Loan repayment",
]


def _get_config() -> dict:
    env = {**dotenv_values(ENV_PATH), **{}}
    return {
        "host": env.get("TAURUS_HOST", "127.0.0.1"),
        "port": int(env.get("TAURUS_PORT", "3306")),
        "db": env.get("TAURUS_DB", "fintech_demo"),
        "user": env.get("TAURUS_USER", "demouser"),
        "password": env.get("DEMO_PASSWORD", ""),
    }


def _connect(cfg: dict) -> pymysql.Connection:
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["db"],
        autocommit=False,
    )


def _already_seeded(cursor) -> bool:
    cursor.execute("SELECT COUNT(*) FROM accounts")
    (count,) = cursor.fetchone()
    return count > 0


def _seed_accounts(cursor, faker: Faker) -> list[int]:
    """Insert 10,000 accounts in batches of 1,000. Returns list of inserted IDs."""
    sql = (
        "INSERT INTO accounts (name, email, balance, risk_score) "
        "VALUES (%s, %s, %s, %s)"
    )
    total_batches = ACCOUNTS_COUNT // BATCH_SIZE
    inserted_ids: list[int] = []

    for batch_num in range(1, total_batches + 1):
        rows = []
        for _ in range(BATCH_SIZE):
            name = faker.name()
            email = faker.unique.email()
            balance = round(random.uniform(100, 500_000), 2)
            risk_score = random.choices(RISK_SCORES, weights=RISK_SCORE_WEIGHTS, k=1)[0]
            rows.append((name, email, balance, risk_score))

        cursor.executemany(sql, rows)
        # Fetch the auto-increment IDs for this batch
        first_id = cursor.lastrowid - BATCH_SIZE + 1
        inserted_ids.extend(range(first_id, first_id + BATCH_SIZE))

        if batch_num % 10 == 0:
            print(
                f"[accounts] batch {batch_num}/{total_batches} "
                f"({batch_num * BATCH_SIZE:,} rows)"
            )

    cursor.connection.commit()
    print(f"[accounts] done — {ACCOUNTS_COUNT:,} rows inserted.")

    cursor.execute("SELECT id FROM accounts ORDER BY id")
    inserted_ids = [row[0] for row in cursor.fetchall()]
    return inserted_ids


def _seed_transactions(cursor, account_ids: list[int], faker: Faker) -> None:
    """Insert 500,000 transactions in batches of 1,000."""
    import datetime

    sql = (
        "INSERT INTO transactions "
        "(account_id, amount, tx_type, description, is_flagged, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    total_batches = TRANSACTIONS_COUNT // BATCH_SIZE
    now = datetime.datetime.utcnow()
    ninety_days = 90 * 24 * 3600  # seconds

    for batch_num in range(1, total_batches + 1):
        rows = []
        for _ in range(BATCH_SIZE):
            account_id = random.choice(account_ids)
            amount = round(random.uniform(5, 25_000), 2)
            tx_type = random.choice(TX_TYPES)
            description = random.choice(DESCRIPTIONS)
            is_flagged = random.random() < 0.02
            offset_secs = random.uniform(0, ninety_days)
            created_at = now - datetime.timedelta(seconds=offset_secs)
            rows.append((account_id, amount, tx_type, description, is_flagged, created_at))

        cursor.executemany(sql, rows)

        if batch_num % 10 == 0:
            print(
                f"[transactions] batch {batch_num}/{total_batches} "
                f"({batch_num * BATCH_SIZE:,} rows)"
            )

    cursor.connection.commit()
    print(f"[transactions] done — {TRANSACTIONS_COUNT:,} rows inserted.")


def main() -> None:
    Faker.seed(1234)
    random.seed(1234)
    faker = Faker()

    cfg = _get_config()
    print(
        f"Connecting to {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['db']} …"
    )
    conn = _connect(cfg)
    cursor = conn.cursor()

    if _already_seeded(cursor):
        print("Database already contains accounts — skipping seed.")
        cursor.close()
        conn.close()
        return

    print("Starting seed …")

    account_ids = _seed_accounts(cursor, faker)
    _seed_transactions(cursor, account_ids, faker)

    cursor.close()
    conn.close()
    print("Seed complete.")


if __name__ == "__main__":
    main()
