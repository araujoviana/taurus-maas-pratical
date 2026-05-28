from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import aiomysql

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(120) NOT NULL,
    email      VARCHAR(180) NOT NULL,
    balance    DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    risk_score TINYINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_email (email)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS transactions (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    account_id  INT NOT NULL,
    amount      DECIMAL(15,2) NOT NULL,
    tx_type     ENUM('credit','debit','transfer') NOT NULL,
    description VARCHAR(255),
    is_flagged  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_account (account_id),
    INDEX idx_created (created_at),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fraud_alerts (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    transaction_id BIGINT,
    account_id     INT,
    alert_type     ENUM('velocity','amount_spike','geo_anomaly','pattern') NOT NULL,
    confidence     FLOAT NOT NULL DEFAULT 0.0,
    reasoning      TEXT,
    detected_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved       BOOLEAN NOT NULL DEFAULT FALSE,
    INDEX idx_account (account_id),
    INDEX idx_detected (detected_at)
) ENGINE=InnoDB;
"""


@dataclass
class DBConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    db: str = "fintech_demo"
    user: str = "demouser"
    password: str = ""
    minsize: int = 2
    maxsize: int = 10


class TaurusDB:
    def __init__(self, config: DBConfig) -> None:
        self._config = config
        self._pool: aiomysql.Pool | None = None

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self._config.host,
            port=self._config.port,
            db=self._config.db,
            user=self._config.user,
            password=self._config.password,
            minsize=self._config.minsize,
            maxsize=self._config.maxsize,
            autocommit=True,
            charset="utf8mb4",
        )

    async def init_schema(self) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                for stmt in SCHEMA_SQL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await cur.execute(stmt)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                return cur.rowcount

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return row

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                return await cur.fetchall()

    async def status(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
                connected = await cur.fetchone()
                await cur.execute("SHOW GLOBAL STATUS LIKE 'Queries'")
                queries = await cur.fetchone()
                await cur.execute("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
                slow = await cur.fetchone()
                return {
                    "threads_connected": int(connected["Value"]) if connected else 0,
                    "queries_per_second": int(queries["Value"]) if queries else 0,
                    "slow_queries": int(slow["Value"]) if slow else 0,
                }
