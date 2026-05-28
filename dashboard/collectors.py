from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

# How often (seconds) to actually ping the MaaS API for a health check.
# Pinging on every 1 s WebSocket tick would be expensive and slow.
MAAS_REFRESH_INTERVAL = 30.0


@dataclass
class TaurusMetrics:
    connected: int = 0
    qps: float = 0.0
    slow_queries: int = 0
    latency_ms: float = 0.0
    errors: int = 0
    available: bool = False


@dataclass
class MAASMetrics:
    latency_ms: float = 0.0
    errors: int = 0
    available: bool = False
    model: str = ""


@dataclass
class ClusterMetrics:
    taurus: TaurusMetrics = field(default_factory=TaurusMetrics)
    maas: MAASMetrics = field(default_factory=MAASMetrics)
    timestamp: float = field(default_factory=time.time)


class TaurusDBCollector:
    """Collects live metrics from TaurusDB via aiomysql.

    QPS is computed as a rate (delta / elapsed) rather than reading the raw
    cumulative ``Queries`` counter from ``SHOW GLOBAL STATUS``.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._prev_queries: int = 0
        self._prev_ts: float = 0.0

    async def collect(self) -> TaurusMetrics:
        try:
            status = await self._db.status()
            start = time.perf_counter()
            await self._db.fetchone("SELECT 1")
            latency = (time.perf_counter() - start) * 1000

            now = time.time()
            raw_queries: int = status["queries_per_second"]  # cumulative counter
            if self._prev_ts > 0 and now > self._prev_ts:
                qps = (raw_queries - self._prev_queries) / (now - self._prev_ts)
            else:
                qps = 0.0
            self._prev_queries = raw_queries
            self._prev_ts = now

            return TaurusMetrics(
                connected=status["threads_connected"],
                qps=round(max(qps, 0.0), 1),
                slow_queries=status["slow_queries"],
                latency_ms=round(latency, 2),
                available=True,
            )
        except Exception:
            return TaurusMetrics(available=False, errors=1)


class MAASCollector:
    """Checks MaaS API health at a slow cadence to avoid hammering the LLM.

    ``collect()`` is safe to call every second — it returns the cached result
    until ``MAAS_REFRESH_INTERVAL`` seconds have elapsed since the last real
    API call.
    """

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._cached: MAASMetrics = MAASMetrics(model=model)
        self._last_check: float = 0.0

    async def collect(self) -> MAASMetrics:
        now = time.time()
        if now - self._last_check < MAAS_REFRESH_INTERVAL:
            return self._cached

        try:
            start = time.perf_counter()
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ),
            )
            latency = (time.perf_counter() - start) * 1000
            self._cached = MAASMetrics(
                latency_ms=round(latency, 2),
                available=True,
                model=self._model,
            )
        except Exception:
            self._cached = MAASMetrics(available=False, errors=1, model=self._model)

        self._last_check = now
        return self._cached
