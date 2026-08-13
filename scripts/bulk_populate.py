"""bulk_populate.py — Adds a large volume of additional accounts/transactions
on top of whatever is already in the database (unlike seed_data.py, this does
not skip if data already exists). Run manually when you want more volume than
the standard demo seed.
"""

import datetime
import random
from pathlib import Path

import pymysql
from dotenv import dotenv_values
from faker import Faker

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

ADDITIONAL_ACCOUNTS = 40_000
ADDITIONAL_TRANSACTIONS = 4_500_000
BATCH_SIZE = 2000

RISK_SCORE_WEIGHTS = [40, 25, 15, 8, 5, 3, 2, 1, 1, 0]
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
    env = dotenv_values(ENV_PATH)
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


def _add_accounts(cursor, faker: Faker, count: int) -> None:
    sql = (
        "INSERT INTO accounts (name, email, balance, risk_score) "
        "VALUES (%s, %s, %s, %s)"
    )
    total_batches = count // BATCH_SIZE

    for batch_num in range(1, total_batches + 1):
        rows = []
        for _ in range(BATCH_SIZE):
            name = faker.name()
            email = faker.unique.email()
            balance = round(random.uniform(100, 500_000), 2)
            risk_score = random.choices(RISK_SCORES, weights=RISK_SCORE_WEIGHTS, k=1)[0]
            rows.append((name, email, balance, risk_score))

        cursor.executemany(sql, rows)

        if batch_num % 10 == 0:
            print(
                f"[accounts] batch {batch_num}/{total_batches} ({batch_num * BATCH_SIZE:,} rows)"
            )

    cursor.connection.commit()
    print(f"[accounts] done — {count:,} additional rows inserted.")


def _add_transactions(cursor, account_ids: list[int], count: int) -> None:
    sql = (
        "INSERT INTO transactions "
        "(account_id, amount, tx_type, description, is_flagged, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    total_batches = count // BATCH_SIZE
    now = datetime.datetime.utcnow()
    ninety_days = 90 * 24 * 3600

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
            rows.append(
                (account_id, amount, tx_type, description, is_flagged, created_at)
            )

        cursor.executemany(sql, rows)

        if batch_num % 50 == 0:
            print(
                f"[transactions] batch {batch_num}/{total_batches} ({batch_num * BATCH_SIZE:,} rows)"
            )
            cursor.connection.commit()

    cursor.connection.commit()
    print(f"[transactions] done — {count:,} additional rows inserted.")


def main() -> None:
    faker = Faker()

    cfg = _get_config()
    print(f"Connecting to {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['db']} …")
    conn = _connect(cfg)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM accounts")
    (existing_accounts,) = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM transactions")
    (existing_transactions,) = cursor.fetchone()
    print(
        f"Existing: {existing_accounts:,} accounts, {existing_transactions:,} transactions"
    )

    _add_accounts(cursor, faker, ADDITIONAL_ACCOUNTS)

    cursor.execute("SELECT id FROM accounts ORDER BY id")
    account_ids = [row[0] for row in cursor.fetchall()]

    _add_transactions(cursor, account_ids, ADDITIONAL_TRANSACTIONS)

    cursor.execute("SELECT COUNT(*) FROM accounts")
    (final_accounts,) = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM transactions")
    (final_transactions,) = cursor.fetchone()

    cursor.close()
    conn.close()
    print(
        f"Bulk populate complete. Totals: {final_accounts:,} accounts, {final_transactions:,} transactions."
    )


if __name__ == "__main__":
    main()
