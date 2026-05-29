from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard.auth import create_token, verify_token
from dashboard.collectors import (
    ClusterMetrics,
    MAASCollector,
    TaurusDBCollector,
)
from dashboard.database import DBConfig, TaurusDB
from dashboard.ai_engine import MaaSClient
from dashboard.scenarios import ScenarioManager

_ENV: dict[str, str] = {}

# HTTPBearer extractor (shared; used by the auth dependency below)
_bearer = HTTPBearer()


def _require_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """FastAPI dependency: verify Bearer JWT against DEMO_PASSWORD (the JWT secret).

    uvicorn binds to 127.0.0.1 so only nginx can reach it; nginx enforces
    HTTP basic auth at the perimeter. This dependency adds a second layer so
    that even a user who already passed nginx basic auth must also hold a
    valid JWT before they can trigger state-changing scenario routes.
    """
    return verify_token(creds, request.app.state.jwt_secret)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ENV
    _ENV = {**dotenv_values(".env"), **os.environ}

    db_config = DBConfig(
        host=_ENV.get("TAURUS_HOST", "127.0.0.1"),
        port=int(_ENV.get("TAURUS_PORT") or "3306"),
        db=_ENV.get("TAURUS_DB", "fintech_demo"),
        user=_ENV.get("TAURUS_USER", "demouser"),
        password=_ENV.get("DEMO_PASSWORD", ""),
    )
    db = TaurusDB(db_config)

    try:
        await db.connect()
        await db.init_schema()
        app.state.db_connected = True
    except Exception:
        app.state.db_connected = False

    app.state.db = db
    app.state.taurus_collector = TaurusDBCollector(db)
    app.state.maas_collector = MAASCollector(
        api_key=_ENV.get("MAAS_API_KEY", ""),
        base_url=_ENV.get("MAAS_BASE_URL", "https://api-ap-southeast-1.modelarts-maas.com/openai/v1"),
        model=_ENV.get("MAAS_MODEL", "glm-5.1"),
    )
    app.state.maas_client = MaaSClient(
        api_key=_ENV.get("MAAS_API_KEY", ""),
        base_url=_ENV.get("MAAS_BASE_URL", "https://api-ap-southeast-1.modelarts-maas.com/openai/v1"),
        model=_ENV.get("MAAS_MODEL", "glm-5.1"),
        db=db,
    )
    app.state.scenario_manager = ScenarioManager(
        db=db,
        maas_api_key=_ENV.get("MAAS_API_KEY", ""),
        maas_base_url=_ENV.get("MAAS_BASE_URL", ""),
        maas_model=_ENV.get("MAAS_MODEL", "glm-5.1"),
        env=_ENV,
    )
    # JWT secret = DEMO_PASSWORD (non-trivial value, localhost-only uvicorn)
    app.state.jwt_secret = _ENV.get("DEMO_PASSWORD", "")
    if not app.state.jwt_secret:
        raise RuntimeError("DEMO_PASSWORD must be set in .env — it is used as the JWT secret")

    yield

    await db.close()


# No CORSMiddleware — the SPA is served from the same origin (nginx proxy).
# Adding wildcard CORS would allow any origin to make credentialed requests.
app = FastAPI(title="TaurusDB + MaaS AI Demo", lifespan=lifespan)

_static = Path(__file__).parent / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


# ── Public routes (no auth required) ────────────────────────────────────────


@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    index = _static / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return {"message": "TaurusDB + MaaS AI Demo running"}


@app.post("/auth/login")
async def login(request: Request):
    """Issue a JWT after verifying credentials."""
    secret = request.app.state.jwt_secret
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        username, password = body.get("username", ""), body.get("password", "")
    else:
        form = await request.form()
        username, password = form.get("username", ""), form.get("password", "")
    if username != "admin" or password != secret:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_token(secret), "token_type": "bearer"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Read-only routes (unauthenticated — perimeter is nginx basic auth) ───────


@app.get("/metrics")
async def get_metrics(request: Request):
    t_col: TaurusDBCollector = request.app.state.taurus_collector
    m_col: MAASCollector = request.app.state.maas_collector
    t_metrics = await t_col.collect()
    m_metrics = await m_col.collect()
    cluster = ClusterMetrics(taurus=t_metrics, maas=m_metrics)
    return json.loads(json.dumps(cluster, default=lambda o: o.__dict__))


@app.get("/scenario/status")
async def scenario_status(request: Request):
    sm: ScenarioManager = request.app.state.scenario_manager
    info = sm.info
    return {
        "state": info.state.value,
        "demo_state": info.demo_state.value,
        "message": info.message,
        "progress": info.progress,
    }


@app.get("/accounts")
async def list_accounts(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
):
    rows = await request.app.state.db.fetchall(
        "SELECT id, name, email, balance, risk_score, created_at FROM accounts ORDER BY id DESC LIMIT %s",
        (limit,),
    )
    return rows


@app.get("/transactions")
async def list_transactions(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
):
    rows = await request.app.state.db.fetchall(
        "SELECT id, account_id, amount, tx_type, description, is_flagged, created_at FROM transactions ORDER BY id DESC LIMIT %s",
        (limit,),
    )
    return rows


@app.get("/db/stats")
async def db_stats(request: Request):
    db: TaurusDB = request.app.state.db
    try:
        status = await db.status()
        count_accounts = await db.fetchone("SELECT COUNT(*) as c FROM accounts")
        count_tx = await db.fetchone("SELECT COUNT(*) as c FROM transactions")
        flagged = await db.fetchone("SELECT COUNT(*) as c FROM transactions WHERE is_flagged = TRUE")
        return {
            **status,
            "total_accounts": count_accounts["c"] if count_accounts else 0,
            "total_transactions": count_tx["c"] if count_tx else 0,
            "flagged_transactions": flagged["c"] if flagged else 0,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ── Mutating routes (JWT required) ───────────────────────────────────────────


@app.post("/scenario/start-load")
async def start_load(request: Request, _: dict = Depends(_require_auth)):
    sm: ScenarioManager = request.app.state.scenario_manager
    asyncio.create_task(sm.start_load())
    return {"status": "started", "scenario": "load"}


@app.post("/scenario/kill-primary")
async def kill_primary(request: Request, _: dict = Depends(_require_auth)):
    sm: ScenarioManager = request.app.state.scenario_manager
    asyncio.create_task(sm.kill_primary())
    return {"status": "started", "scenario": "failover"}


@app.post("/scenario/ai-analyze")
async def ai_analyze(request: Request, _: dict = Depends(_require_auth)):
    sm: ScenarioManager = request.app.state.scenario_manager
    result = await sm.ai_analyze()
    return result


@app.post("/scenario/reset")
async def reset(request: Request, _: dict = Depends(_require_auth)):
    sm: ScenarioManager = request.app.state.scenario_manager
    await sm.reset()
    return {"status": "reset"}


class ChatRequest(BaseModel):
    message: str
    history: list = []


@app.post("/ai/chat")
async def ai_chat(request: Request, body: ChatRequest, _: dict = Depends(_require_auth)):
    client: MaaSClient = request.app.state.maas_client
    result = await client.chat(body.message, body.history)
    return result


@app.get("/ai/commentary")
async def get_commentary(request: Request):
    client: MaaSClient = request.app.state.maas_client
    t_col: TaurusDBCollector = request.app.state.taurus_collector
    sm: ScenarioManager = request.app.state.scenario_manager
    try:
        t_metrics = await t_col.collect()
        metrics_snapshot = {
            "qps": t_metrics.qps,
            "latency_ms": t_metrics.latency_ms,
            "connections": t_metrics.connected,
            "slow_queries": t_metrics.slow_queries,
            "scenario_state": sm.info.state.value,
        }
    except Exception:
        metrics_snapshot = {"qps": 0, "latency_ms": 0, "connections": 0, "slow_queries": 0, "scenario_state": "idle"}
    text = await client.get_commentary(metrics_snapshot)
    return {"text": text, "ts": time.time()}


@app.get("/ai/report")
async def get_report(request: Request, _: dict = Depends(_require_auth)):
    client: MaaSClient = request.app.state.maas_client
    report = await client.generate_report()
    return report


@app.post("/fraud/inject/{pattern}")
async def inject_fraud(request: Request, pattern: str, _: dict = Depends(_require_auth)):
    from scenarios.fraud_injection import PATTERNS
    if pattern not in PATTERNS:
        raise HTTPException(status_code=400, detail=f"Unknown pattern: {pattern}. Use: velocity, large_transfer, geo_anomaly")
    inject_fn, description = PATTERNS[pattern]
    db: TaurusDB = request.app.state.db
    result = await asyncio.get_running_loop().run_in_executor(
        None, inject_fn, db, _ENV
    )
    return result


@app.post("/fraud/analyze")
async def fraud_analyze(request: Request, _: dict = Depends(_require_auth)):
    client: MaaSClient = request.app.state.maas_client
    result = await client.analyze_anomalies()
    return result


@app.get("/fraud/alerts")
async def get_fraud_alerts(request: Request, limit: int = Query(50, ge=1, le=200)):
    db: TaurusDB = request.app.state.db
    try:
        rows = await db.fetchall(
            "SELECT id, transaction_id, account_id, alert_type, confidence, reasoning, detected_at, resolved "
            "FROM fraud_alerts ORDER BY detected_at DESC LIMIT %s",
            (limit,),
        )
        return rows
    except Exception as exc:
        return {"error": str(exc)}


# ── WebSocket (reads token from ?token= query parameter) ─────────────────────


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str | None = None):
    """Live metrics stream.

    Accepts a Bearer token via the `?token=<jwt>` query parameter (WebSocket
    clients cannot set HTTP headers, so query params are the standard pattern).
    Unauthenticated connections are accepted but get metrics-only data —
    the same data nginx already serves to anyone who passed basic auth.
    """
    await ws.accept()

    # Optionally validate token (don't disconnect on missing — the presenter
    # already authenticated via nginx basic auth to reach this page)
    if token:
        try:
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            verify_token(creds, ws.app.state.jwt_secret)
        except HTTPException:
            await ws.close(code=1008)  # Policy Violation
            return

    t_col: TaurusDBCollector = ws.app.state.taurus_collector
    m_col: MAASCollector = ws.app.state.maas_collector
    sm: ScenarioManager = ws.app.state.scenario_manager
    try:
        while True:
            t_metrics = await t_col.collect()
            m_metrics = await m_col.collect()
            payload = {
                "taurus": {
                    "connected": t_metrics.connected,
                    "qps": t_metrics.qps,
                    "slow_queries": t_metrics.slow_queries,
                    "latency_ms": t_metrics.latency_ms,
                    "available": t_metrics.available,
                    "errors": t_metrics.errors,
                },
                "maas": {
                    "latency_ms": m_metrics.latency_ms,
                    "available": m_metrics.available,
                    "model": m_metrics.model,
                    "errors": m_metrics.errors,
                },
                "scenario": {
                    "state": sm.info.state.value,
                    "demo_state": sm.info.demo_state.value,
                    "message": sm.info.message,
                    "progress": sm.info.progress,
                },
                "commentary": getattr(ws.app.state, "maas_client", None) and ws.app.state.maas_client._cached_commentary or "",
                "ts": time.time(),
            }
            await ws.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
