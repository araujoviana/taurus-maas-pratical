from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class ScenarioState(enum.Enum):
    IDLE = "idle"
    LOADING = "loading"
    FAILING_OVER = "failing_over"
    AI_ANALYZING = "ai_analyzing"
    SCALING = "scaling"


class DemoState(enum.Enum):
    READY = "ready"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ScenarioInfo:
    state: ScenarioState = ScenarioState.IDLE
    demo_state: DemoState = DemoState.READY
    message: str = ""
    progress: float = 0.0
    load_start: float = 0.0
    load_end: float = 0.0
    failover_start: float = 0.0
    failover_end: float = 0.0
    recovery_start: float = 0.0
    recovery_end: float = 0.0
    ai_start: float = 0.0
    ai_end: float = 0.0


class ScenarioManager:
    def __init__(
        self,
        db: Any = None,
        maas_api_key: str = "",
        maas_base_url: str = "",
        maas_model: str = "",
        env: dict[str, str] | None = None,
    ) -> None:
        self._db = db
        self._maas_api_key = maas_api_key
        self._maas_base_url = maas_base_url
        self._maas_model = maas_model
        self._env = env or {}
        self.info = ScenarioInfo()

    async def start_load(self) -> None:
        if self.info.state != ScenarioState.IDLE:
            return
        self.info.state = ScenarioState.LOADING
        self.info.demo_state = DemoState.RUNNING
        self.info.message = "Starting transaction load..."
        self.info.progress = 0.0
        self.info.load_start = time.time()
        try:
            from scenarios.workload import run_workload
            await asyncio.get_running_loop().run_in_executor(
                None,
                run_workload,
                self._db,
                self._env,
                self._on_load_progress,
            )
        except Exception as exc:
            self.info.demo_state = DemoState.ERROR
            self.info.message = f"Load error: {exc}"
            return
        self.info.load_end = time.time()
        self.info.state = ScenarioState.IDLE
        self.info.demo_state = DemoState.COMPLETE
        self.info.message = "Load complete"
        self.info.progress = 100.0

    def _on_load_progress(self, pct: float) -> None:
        self.info.progress = pct

    async def kill_primary(self) -> None:
        if self.info.state != ScenarioState.IDLE:
            return
        self.info.state = ScenarioState.FAILING_OVER
        self.info.demo_state = DemoState.RUNNING
        self.info.message = "Simulating primary failure..."
        self.info.failover_start = time.time()
        try:
            from scenarios.failover import run_failover
            await asyncio.get_running_loop().run_in_executor(
                None,
                run_failover,
                self._env,
            )
        except Exception as exc:
            self.info.demo_state = DemoState.ERROR
            self.info.message = f"Failover error: {exc}"
            return
        self.info.failover_end = time.time()
        self.info.recovery_start = self.info.failover_end
        self.info.message = "Monitoring recovery..."
        try:
            from scenarios.failover import wait_recovery
            await asyncio.get_running_loop().run_in_executor(
                None,
                wait_recovery,
                self._db,
                self._env,
            )
        except Exception:
            pass
        self.info.recovery_end = time.time()
        self.info.state = ScenarioState.IDLE
        self.info.demo_state = DemoState.COMPLETE
        self.info.message = "Failover & recovery complete"

    async def ai_analyze(self) -> dict[str, Any]:
        if self.info.state != ScenarioState.IDLE:
            return {"error": "Scenario already running"}
        self.info.state = ScenarioState.AI_ANALYZING
        self.info.demo_state = DemoState.RUNNING
        self.info.message = "Running AI fraud analysis..."
        self.info.ai_start = time.time()
        try:
            from scenarios.ai_analytics import run_ai_analysis
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                run_ai_analysis,
                self._db,
                self._maas_api_key,
                self._maas_base_url,
                self._maas_model,
            )
        except Exception as exc:
            self.info.demo_state = DemoState.ERROR
            self.info.message = f"AI error: {exc}"
            return {"error": str(exc)}
        self.info.ai_end = time.time()
        self.info.state = ScenarioState.IDLE
        self.info.demo_state = DemoState.COMPLETE
        self.info.message = "AI analysis complete"
        return result

    async def reset(self) -> None:
        self.info = ScenarioInfo()
