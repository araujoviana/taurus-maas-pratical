from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dashboard.scenarios import (
    DemoState,
    ScenarioInfo,
    ScenarioManager,
    ScenarioState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager(**kwargs) -> ScenarioManager:
    return ScenarioManager(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_initial_state():
    sm = _manager()
    assert sm.info.state == ScenarioState.IDLE
    assert sm.info.demo_state == DemoState.READY


async def test_start_load_idempotent():
    sm = _manager()
    sm.info.state = ScenarioState.LOADING
    sm.info.demo_state = DemoState.RUNNING
    sm.info.message = "already loading"

    await sm.start_load()

    # State must not have changed — the guard returned early
    assert sm.info.state == ScenarioState.LOADING
    assert sm.info.message == "already loading"


async def test_start_load_calls_workload():
    sm = _manager()

    mock_run_workload = MagicMock(return_value=None)

    with patch.dict(
        "sys.modules", {"scenarios.workload": MagicMock(run_workload=mock_run_workload)}
    ):
        with patch("scenarios.workload.run_workload", mock_run_workload, create=True):
            # Patch via the import path used inside start_load
            with patch(
                "dashboard.scenarios.ScenarioManager.start_load", wraps=sm.start_load
            ):
                # We need to intercept the dynamic import inside start_load
                import sys

                fake_workload_module = MagicMock()
                fake_workload_module.run_workload = mock_run_workload
                sys.modules["scenarios.workload"] = fake_workload_module

                await sm.start_load()

    assert sm.info.state == ScenarioState.IDLE
    assert sm.info.demo_state == DemoState.COMPLETE
    assert sm.info.load_start > 0
    assert sm.info.load_end > 0
    assert sm.info.progress == 100.0


async def test_kill_primary_sets_failing_over_state():
    sm = _manager()

    import sys

    fake_failover_module = MagicMock()
    fake_failover_module.run_failover = MagicMock(return_value=None)
    fake_failover_module.wait_recovery = MagicMock(return_value=None)
    sys.modules["scenarios.failover"] = fake_failover_module

    try:
        await sm.kill_primary()
    finally:
        # Clean up so other tests aren't affected
        sys.modules.pop("scenarios.failover", None)

    assert sm.info.failover_start > 0
    assert sm.info.failover_end > 0
    assert sm.info.recovery_end > 0
    assert sm.info.state == ScenarioState.IDLE
    assert sm.info.demo_state == DemoState.COMPLETE


async def test_ai_analyze_when_busy():
    sm = _manager()
    sm.info.state = ScenarioState.LOADING

    result = await sm.ai_analyze()

    assert result == {"error": "Scenario already running"}
    # State should not have changed
    assert sm.info.state == ScenarioState.LOADING


async def test_reset_clears_state():
    sm = _manager()
    sm.info.state = ScenarioState.FAILING_OVER
    sm.info.demo_state = DemoState.ERROR
    sm.info.message = "something happened"
    sm.info.progress = 42.0

    await sm.reset()

    assert sm.info.state == ScenarioState.IDLE
    assert sm.info.demo_state == DemoState.READY
    assert sm.info.message == ""
    assert sm.info.progress == 0.0
