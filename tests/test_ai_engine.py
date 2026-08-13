from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.ai_engine import (
    MaaSClient,
    _validate_sql,
    TOOL_DEFINITIONS,
    REPORT_TOOL_DEFINITIONS,
    REPORT_SECTION_PROMPTS,
)


def _mock_db():
    db = MagicMock()
    db.fetchall = AsyncMock(return_value=[])
    db.fetchone = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=1)
    db.status = AsyncMock(
        return_value={
            "threads_connected": 5,
            "queries_per_second": 1000,
            "slow_queries": 2,
        }
    )
    return db


def _mock_openai_response(content="test", tool_calls=None):
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(total_tokens=50)
    return response


def _mock_tool_call(name, args_dict):
    tc = MagicMock()
    tc.id = "call_123"
    tc.function.name = name
    tc.function.arguments = json.dumps(args_dict)
    return tc


# ---------------------------------------------------------------------------
# SQL safety validation
# ---------------------------------------------------------------------------


def test_validate_sql_select_allowed():
    result = _validate_sql("SELECT * FROM accounts")
    assert result.startswith("SELECT")


def test_validate_sql_adds_limit():
    result = _validate_sql("SELECT * FROM accounts")
    assert "LIMIT 200" in result


def test_validate_sql_preserves_existing_limit():
    result = _validate_sql("SELECT * FROM accounts LIMIT 10")
    assert "LIMIT 10" in result
    assert "LIMIT 200" not in result


def test_validate_sql_rejects_insert():
    with pytest.raises(ValueError, match="Only SELECT"):
        _validate_sql("INSERT INTO accounts VALUES (1)")


def test_validate_sql_rejects_update():
    with pytest.raises(ValueError, match="Only SELECT"):
        _validate_sql("UPDATE accounts SET balance = 0")


def test_validate_sql_rejects_delete():
    with pytest.raises(ValueError, match="Only SELECT"):
        _validate_sql("DELETE FROM accounts")


def test_validate_sql_rejects_drop():
    with pytest.raises(ValueError, match="Only SELECT"):
        _validate_sql("DROP TABLE accounts")


def test_validate_sql_rejects_smuggled_statement():
    with pytest.raises(ValueError, match="forbidden keywords"):
        _validate_sql("SELECT * FROM accounts; DROP TABLE accounts")


def test_validate_sql_case_insensitive():
    with pytest.raises(ValueError):
        _validate_sql("insert into accounts values (1)")


def test_validate_sql_strips_whitespace():
    result = _validate_sql("  SELECT id FROM accounts  ")
    assert result.startswith("SELECT")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def test_tool_definitions_exist():
    names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    assert "run_sql" in names
    assert "get_db_metrics" in names
    assert "flag_transaction" in names
    assert "get_account_details" in names


# ---------------------------------------------------------------------------
# MaaSClient tool execution
# ---------------------------------------------------------------------------


async def test_execute_run_sql():
    db = _mock_db()
    db.fetchall = AsyncMock(return_value=[{"id": 1, "name": "Alice"}])
    with patch("dashboard.ai_engine.AsyncOpenAI"):
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)
    result = await client._execute_tool(
        "run_sql", {"query": "SELECT * FROM accounts LIMIT 5"}
    )
    parsed = json.loads(result)
    assert parsed["row_count"] == 1


async def test_execute_run_sql_rejects_non_select():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI"):
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)
    result = await client._execute_tool("run_sql", {"query": "DELETE FROM accounts"})
    parsed = json.loads(result)
    assert "error" in parsed


async def test_execute_get_db_metrics():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI"):
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)
    result = await client._execute_tool("get_db_metrics", {})
    parsed = json.loads(result)
    assert parsed["threads_connected"] == 5


async def test_execute_flag_transaction():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI"):
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)
    result = await client._execute_tool(
        "flag_transaction",
        {
            "transaction_id": 42,
            "alert_type": "velocity",
            "confidence": 0.95,
            "reasoning": "Rapid micro-transactions",
        },
    )
    parsed = json.loads(result)
    assert parsed["status"] == "flagged"
    assert parsed["transaction_id"] == 42
    db.execute.assert_called_once()


async def test_execute_get_account_details():
    db = _mock_db()
    db.fetchone = AsyncMock(return_value={"id": 1, "name": "Alice"})
    db.fetchall = AsyncMock(return_value=[{"id": 100, "amount": 500}])
    with patch("dashboard.ai_engine.AsyncOpenAI"):
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)
    result = await client._execute_tool("get_account_details", {"account_id": 1})
    parsed = json.loads(result)
    assert parsed["account"]["name"] == "Alice"


async def test_execute_unknown_tool():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI"):
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)
    result = await client._execute_tool("nonexistent", {})
    parsed = json.loads(result)
    assert "error" in parsed


# ---------------------------------------------------------------------------
# MaaSClient._run_tool_loop error handling
# ---------------------------------------------------------------------------


async def test_run_tool_loop_handles_malformed_tool_arguments():
    """A malformed JSON tool-call argument must not crash the loop — it should
    feed a tool-error message back so the model can retry, and the loop should
    still reach a final answer on the next iteration."""
    db = _mock_db()
    bad_tc = MagicMock()
    bad_tc.id = "call_bad"
    bad_tc.function.name = "run_sql"
    bad_tc.function.arguments = "{not valid json"

    first_response = _mock_openai_response(content="", tool_calls=[bad_tc])
    second_response = _mock_openai_response(content="Recovered after invalid args")

    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[first_response, second_response]
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    result = await client._run_tool_loop(messages)

    assert result["content"] == "Recovered after invalid args"
    assert mock_client.chat.completions.create.call_count == 2

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    parsed = json.loads(tool_msgs[0]["content"])
    assert "error" in parsed


# ---------------------------------------------------------------------------
# MaaSClient chat
# ---------------------------------------------------------------------------


async def test_chat_no_tool_calls():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Top 5 accounts by risk: Alice, Bob")
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result = await client.chat("Show top accounts by risk", [])
    assert "text" in result
    assert result["text"] == "Top 5 accounts by risk: Alice, Bob"
    assert result["tool_calls_made"] == []


async def test_chat_with_chart_extraction():
    db = _mock_db()
    response_text = 'Here are the results.\nCHART_JSON: {"type":"bar","title":"Risk","categories":["A"],"series":[{"name":"R","data":[9]}]}'
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response(response_text)
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result = await client.chat("Show risk", [])
    assert result["chart"] is not None
    assert result["chart"]["type"] == "bar"
    assert "CHART_JSON" not in result["text"]


# ---------------------------------------------------------------------------
# MaaSClient commentary
# ---------------------------------------------------------------------------


async def test_get_commentary():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("TaurusDB handling 8K QPS at p99 < 3ms.")
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result = await client.get_commentary({"qps": 8000, "latency_ms": 2.5})
    assert "8K QPS" in result


async def test_get_commentary_uses_cache():
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Cached message")
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result1 = await client.get_commentary({"qps": 100})
    result2 = await client.get_commentary({"qps": 200})
    assert result1 == result2
    assert mock_client.chat.completions.create.call_count == 1


async def test_get_commentary_ttl_scenario_aware():
    """Cache TTL should be short (6s) while a scenario is active, long (25s) at idle."""
    db = _mock_db()
    with (
        patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI,
        patch("dashboard.ai_engine.time.time") as mock_time,
    ):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                _mock_openai_response("msg1"),
                _mock_openai_response("msg2"),
            ]
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

        # First call at t=0 (idle) — cache miss, populates cache.
        mock_time.return_value = 0
        result1 = await client.get_commentary({"qps": 1, "scenario_state": "idle"})
        assert result1 == "msg1"
        assert mock_client.chat.completions.create.call_count == 1

        # 10s later, still idle: 10s < 25s idle TTL -> cache hit, no new call.
        mock_time.return_value = 10
        result2 = await client.get_commentary({"qps": 1, "scenario_state": "idle"})
        assert result2 == "msg1"
        assert mock_client.chat.completions.create.call_count == 1

        # 11s since last refresh, but now a scenario is active: 11s >= 6s active
        # TTL -> cache must be treated as stale even though it would still be
        # fresh under the idle TTL.
        mock_time.return_value = 11
        result3 = await client.get_commentary({"qps": 1, "scenario_state": "loading"})
        assert result3 == "msg2"
        assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# MaaSClient analyze_anomalies
# ---------------------------------------------------------------------------


async def test_analyze_anomalies():
    db = _mock_db()
    db.fetchall = AsyncMock(return_value=[])
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response(
                "No anomalies detected in the last 30 minutes."
            )
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result = await client.analyze_anomalies()
    assert "alerts_created" in result
    assert "summary" in result
    assert "alerts" in result


# ---------------------------------------------------------------------------
# MaaSClient generate_report
# ---------------------------------------------------------------------------


async def test_generate_report():
    """generate_report() now runs 4 concurrent _run_tool_loop calls (one per
    section) instead of 6 canned queries + 1 narrative call + string parsing."""
    db = _mock_db()
    db.fetchall = AsyncMock(return_value=[{"total": 1000}])
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_response("Section body text.")
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result = await client.generate_report()

    assert "sections" in result
    assert "generated_at" in result
    assert "data" not in result

    titles = {s["title"] for s in result["sections"]}
    assert titles == set(REPORT_SECTION_PROMPTS.keys())
    assert len(result["sections"]) == 4
    for section in result["sections"]:
        assert section["content"] == "Section body text."

    # One tool-loop iteration per section — no tool calls in the mocked
    # response, so each section makes exactly one chat.completions.create call.
    assert mock_client.chat.completions.create.call_count == 4


async def test_generate_report_section_failure_is_graceful():
    """If one section's tool loop raises, it should degrade to a fallback
    message instead of failing the whole report."""
    db = _mock_db()
    with patch("dashboard.ai_engine.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("MaaS API unreachable")
        )
        MockOpenAI.return_value = mock_client
        client = MaaSClient(api_key="k", base_url="u", model="m", db=db)

    result = await client.generate_report()
    assert len(result["sections"]) == 4
    for section in result["sections"]:
        assert section["content"] == "Unable to generate this section."


def test_report_tool_definitions_excludes_flag_transaction():
    names = [t["function"]["name"] for t in REPORT_TOOL_DEFINITIONS]
    assert "flag_transaction" not in names
    assert "run_sql" in names
    assert "get_db_metrics" in names
    assert "get_account_details" in names
