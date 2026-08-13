from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.collectors import (
    MAAS_REFRESH_INTERVAL,
    MAASCollector,
    TaurusDBCollector,
    TaurusMetrics,
    MAASMetrics,
)

# ---------------------------------------------------------------------------
# TaurusDBCollector tests
# ---------------------------------------------------------------------------


async def _make_taurus_db(status_data: dict, fetchone_return=None, raise_exc=None):
    db = MagicMock()
    if raise_exc:
        db.status = AsyncMock(side_effect=raise_exc)
    else:
        db.status = AsyncMock(return_value=status_data)
        db.fetchone = AsyncMock(return_value=fetchone_return or {"1": 1})
    return db


async def test_taurus_collector_success():
    status_data = {
        "threads_connected": 5,
        "queries_per_second": 1000,
        "slow_queries": 2,
    }
    db = await _make_taurus_db(status_data)
    collector = TaurusDBCollector(db)

    metrics = await collector.collect()

    assert metrics.available is True
    assert metrics.connected == 5
    assert metrics.slow_queries == 2
    assert metrics.latency_ms >= 0.0
    assert metrics.errors == 0


async def test_taurus_collector_qps_rate():
    # First call: 1000 cumulative queries
    status_first = {
        "threads_connected": 3,
        "queries_per_second": 1000,
        "slow_queries": 0,
    }
    # Second call: 1060 cumulative queries — 60 more over ~1 second → QPS ~60
    status_second = {
        "threads_connected": 3,
        "queries_per_second": 1060,
        "slow_queries": 0,
    }

    db = MagicMock()
    db.fetchone = AsyncMock(return_value={"1": 1})

    fake_times = [100.0, 101.0]  # time.time() returns for 1st call, 2nd call
    time_iter = iter(fake_times)

    with patch("dashboard.collectors.time") as mock_time:
        mock_time.time.side_effect = lambda: next(time_iter)
        mock_time.perf_counter.return_value = 0.0

        # First collect
        db.status = AsyncMock(return_value=status_first)
        collector = TaurusDBCollector(db)
        metrics1 = await collector.collect()
        assert metrics1.qps == 0.0  # no previous data point

        # Second collect — 1 second elapsed, 60 extra queries
        db.status = AsyncMock(return_value=status_second)
        metrics2 = await collector.collect()
        assert metrics2.qps > 0.0
        assert metrics2.qps == round((1060 - 1000) / (101.0 - 100.0), 1)


async def test_taurus_collector_db_failure():
    db = await _make_taurus_db({}, raise_exc=RuntimeError("connection refused"))
    collector = TaurusDBCollector(db)

    metrics = await collector.collect()

    assert metrics.available is False
    assert metrics.errors == 1


# ---------------------------------------------------------------------------
# MAASCollector tests
# ---------------------------------------------------------------------------


def _make_maas_collector(api_key="key", base_url="http://maas", model="gpt-test"):
    """Create a MAASCollector with a mocked OpenAI client."""
    with patch("dashboard.collectors.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        collector = MAASCollector(api_key=api_key, base_url=base_url, model=model)
    return collector, mock_client


async def test_maas_collector_uses_cache():
    collector, mock_client = _make_maas_collector()

    # Configure the completions call to succeed
    mock_client.chat.completions.create.return_value = MagicMock()

    # First call — should hit the API
    metrics1 = await collector.collect()
    assert mock_client.chat.completions.create.call_count == 1

    # Second call immediately — should use cache, no new API call
    metrics2 = await collector.collect()
    assert mock_client.chat.completions.create.call_count == 1

    assert metrics2.available is True


async def test_maas_collector_refreshes_after_interval():
    collector, mock_client = _make_maas_collector()
    mock_client.chat.completions.create.return_value = MagicMock()

    # Pre-warm the cache with a stale timestamp
    collector._last_check = time.time() - (MAAS_REFRESH_INTERVAL + 1)
    collector._cached = MAASMetrics(available=True, model="gpt-test")

    metrics = await collector.collect()

    assert mock_client.chat.completions.create.call_count == 1
    assert metrics.available is True


async def test_maas_collector_api_failure():
    collector, mock_client = _make_maas_collector()
    mock_client.chat.completions.create.side_effect = RuntimeError("API down")

    # Force cache to be expired so the real call is made
    collector._last_check = 0.0

    metrics = await collector.collect()

    assert metrics.available is False
    assert metrics.errors == 1
