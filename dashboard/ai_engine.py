from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from openai import AsyncOpenAI

from dashboard.database import TaurusDB

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 5
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b", re.IGNORECASE
)

CHAT_SYSTEM_PROMPT = """You are a TaurusDB AI analyst for a fintech platform. You have access to a MySQL-compatible database.
When a user asks a business question:
1. Call run_sql with the appropriate SELECT query
2. Analyze the results
3. Return a clear text answer
4. If the data is chartable, include chart metadata in your response

Database schema:
- accounts(id, name, email, balance, risk_score 0-9, created_at) — 10k rows
- transactions(id, account_id, amount, tx_type, description, is_flagged, created_at) — 500k rows
- fraud_alerts(id, transaction_id, account_id, alert_type, confidence, reasoning, detected_at, resolved)

Always use LIMIT in queries (max 500 rows). Only SELECT — never INSERT/UPDATE/DELETE.
When choosing chart type: time-series → line, ranked items → bar, proportions → pie, raw data → table.

IMPORTANT: After you get SQL results, include a JSON chart spec in your response like:
CHART_JSON: {"type":"bar","title":"Top Accounts","categories":["A","B"],"series":[{"name":"Risk","data":[9,8]}]}
Choose chart type based on data shape: time-series → line, ranked → bar, proportions → pie, tabular → table."""

ANOMALY_SYSTEM_PROMPT = """You are a fraud detection AI for a fintech platform. Analyze recent transactions for anomalies.
Use your tools to:
1. Run SQL queries to find suspicious patterns in the last 60 minutes
2. Get account details for suspicious accounts
3. Flag confirmed threats using flag_transaction

Look for:
- Velocity attacks: many rapid small transactions from one account
- Amount spikes: single transactions far above account average
- Geographic anomalies: transactions from multiple countries in short time
- Pattern fraud: unusual transaction patterns

After analysis, summarize what you found and how many alerts you created."""

COMMENTARY_SYSTEM_PROMPT = """You are a live technical analyst narrating a fintech database performance demo.
Given current metrics, write ONE sentence (max 120 chars) in a professional but engaging tone.
Mix technical precision with business impact.
Examples:
- "TaurusDB is absorbing 12,400 QPS at p99 < 3ms — processing ~750M transactions/day at this rate."
- "Connection pooling is holding steady at 47 active connections across 500k concurrent load."
- "Standby promotion completed in 18 seconds — zero application reconnect required."
When a scenario_message and progress are provided and a scenario is actively running, narrate that
specific event concretely instead of generic steady-state commentary. Examples:
- "Primary failover in progress (62%) — standby promotion detected, application traffic draining from the dead node."
- "Bulk load in progress (34%) — 170,000 of 500,000 transactions written, QPS climbing as inserts pipeline."
Use max_tokens=80. No tool calls needed."""

REPORT_SECTION_PROMPTS = {
    "Executive Summary": (
        "You are a BI analyst writing the Executive Summary section of an executive report "
        "for a fintech platform. Investigate overall financial health: revenue, transaction "
        "volume/velocity, and account growth. You have run_sql, get_db_metrics, and "
        "get_account_details tools — decide what queries are relevant and run them yourself "
        "before writing. Write in clear, professional language suitable for C-suite "
        "presentation."
    ),
    "Risk Assessment": (
        "You are a BI analyst writing the Risk Assessment section of an executive report for "
        "a fintech platform. Investigate fraud and risk exposure: query fraud_alerts (alert "
        "types, volume, confidence) and the risk_score distribution on accounts. You have "
        "run_sql, get_db_metrics, and get_account_details tools — decide what queries are "
        "relevant and run them yourself before writing. Write in clear, professional language "
        "suitable for C-suite presentation."
    ),
    "Top Opportunities": (
        "You are a BI analyst writing the Top Opportunities section of an executive report "
        "for a fintech platform. Investigate growth areas: top merchants/transaction types by "
        "volume, and segments showing growth. You have run_sql, get_db_metrics, and "
        "get_account_details tools — decide what queries are relevant and run them yourself "
        "before writing. Write in clear, professional language suitable for C-suite "
        "presentation."
    ),
    "Recommendations": (
        "You are a BI analyst writing the Recommendations section of an executive report for "
        "a fintech platform. Synthesize 3-5 actionable next steps based on typical fintech "
        "platform risk/growth tradeoffs. This section is primarily synthesis, not fresh "
        "investigation — you have run_sql, get_db_metrics, and get_account_details tools "
        "available if a quick check would sharpen a recommendation, but you do not need to "
        "query extensively. Write in clear, professional language suitable for C-suite "
        "presentation."
    ),
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run a READ-ONLY SELECT query against the TaurusDB database. Returns up to 200 rows as a list of dicts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A SQL SELECT query. Only SELECT statements are allowed. LIMIT 200 is added if no LIMIT present.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_db_metrics",
            "description": "Get current TaurusDB metrics snapshot including QPS, connections, and slow queries.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_transaction",
            "description": "Flag a transaction as fraud by inserting a row into fraud_alerts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {
                        "type": "integer",
                        "description": "The transaction ID to flag",
                    },
                    "alert_type": {
                        "type": "string",
                        "enum": ["velocity", "amount_spike", "geo_anomaly", "pattern"],
                        "description": "Type of fraud alert",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score 0.0-1.0",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why this transaction is flagged",
                    },
                },
                "required": ["transaction_id", "alert_type", "confidence", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_details",
            "description": "Get account details plus last 20 transactions for a given account ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "integer",
                        "description": "The account ID to look up",
                    }
                },
                "required": ["account_id"],
            },
        },
    },
]

REPORT_TOOL_DEFINITIONS = [
    t for t in TOOL_DEFINITIONS if t["function"]["name"] != "flag_transaction"
]


def _validate_sql(query: str) -> str:
    stripped = query.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise ValueError("Query contains forbidden keywords")
    if "LIMIT" not in stripped.upper():
        stripped = stripped.rstrip(";") + " LIMIT 200"
    return stripped


class MaaSClient:
    def __init__(self, api_key: str, base_url: str, model: str, db: TaurusDB) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._db = db
        self._cached_commentary: str = ""
        self._commentary_ts: float = 0.0

    async def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "run_sql":
            try:
                safe_query = _validate_sql(args["query"])
                rows = await self._db.fetchall(safe_query)
                return json.dumps(
                    {"row_count": len(rows), "data": rows[:200]}, default=str
                )
            except ValueError as e:
                return json.dumps({"error": str(e)})
            except Exception as e:
                return json.dumps({"error": f"SQL error: {e}"})

        elif tool_name == "get_db_metrics":
            try:
                status = await self._db.status()
                return json.dumps({**status, "timestamp": time.time()})
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif tool_name == "flag_transaction":
            try:
                account_id = args.get("account_id")
                if account_id is None:
                    row = await self._db.fetchone(
                        "SELECT account_id FROM transactions WHERE id = %s",
                        (args["transaction_id"],),
                    )
                    account_id = row["account_id"] if row else None
                await self._db.execute(
                    "INSERT INTO fraud_alerts (transaction_id, account_id, alert_type, confidence, reasoning) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        args["transaction_id"],
                        account_id,
                        args["alert_type"],
                        args["confidence"],
                        args["reasoning"],
                    ),
                )
                return json.dumps(
                    {"status": "flagged", "transaction_id": args["transaction_id"]}
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif tool_name == "get_account_details":
            try:
                account = await self._db.fetchone(
                    "SELECT * FROM accounts WHERE id = %s", (args["account_id"],)
                )
                txns = await self._db.fetchall(
                    "SELECT * FROM transactions WHERE account_id = %s ORDER BY created_at DESC LIMIT 20",
                    (args["account_id"],),
                )
                return json.dumps(
                    {"account": account, "recent_transactions": txns}, default=str
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def _run_tool_loop(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> dict:
        tool_calls_made: list[str] = []
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools or TOOL_DEFINITIONS,
                temperature=0.3,
            )
            choice = response.choices[0]
            assistant_msg = {
                "role": "assistant",
                "content": choice.message.content or "",
            }
            if choice.message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]
            messages.append(assistant_msg)

            if not choice.message.tool_calls:
                return {
                    "content": choice.message.content or "",
                    "tool_calls_made": tool_calls_made,
                }

            for tc in choice.message.tool_calls:
                tool_calls_made.append(tc.function.name)
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(
                                {
                                    "error": "Malformed tool arguments, please retry with valid JSON"
                                }
                            ),
                        }
                    )
                    continue
                result = await self._execute_tool(tc.function.name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )

        return {
            "content": messages[-1].get("content", ""),
            "tool_calls_made": tool_calls_made,
        }

    def _extract_chart(self, text: str) -> dict | None:
        marker = "CHART_JSON:"
        idx = text.find(marker)
        if idx == -1:
            return None
        try:
            start = idx + len(marker)
            rest = text[start:].strip()
            depth = 0
            end = -1
            for i, ch in enumerate(rest):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end == -1:
                return None
            chart_json = json.loads(rest[: end + 1])
            return chart_json
        except (json.JSONDecodeError, ValueError):
            return None

    def _extract_sql(self, text: str) -> str | None:
        for msg_marker in ["```sql", "```SQL", "```"]:
            idx = text.find(msg_marker)
            if idx != -1:
                start = idx + len(msg_marker)
                end = text.find("```", start)
                if end != -1:
                    return text[start:end].strip()
        return None

    async def chat(self, message: str, history: list) -> dict:
        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        for h in history:
            messages.append(h)
        messages.append({"role": "user", "content": message})

        result = await self._run_tool_loop(messages)
        text = result["content"]
        chart = self._extract_chart(text)
        sql = self._extract_sql(text)
        clean_text = text
        if "CHART_JSON:" in clean_text:
            idx = clean_text.find("CHART_JSON:")
            clean_text = clean_text[:idx].strip()

        return {
            "text": clean_text,
            "sql": sql,
            "chart": chart,
            "tool_calls_made": result["tool_calls_made"],
        }

    async def analyze_anomalies(self, window_minutes: int = 30) -> dict:
        messages = [
            {"role": "system", "content": ANOMALY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Analyze transactions from the last {window_minutes} minutes for fraud patterns. "
                "Use your tools to query the database and flag any suspicious activity.",
            },
        ]
        result = await self._run_tool_loop(messages)

        try:
            alerts = await self._db.fetchall(
                "SELECT id, transaction_id, account_id, alert_type, confidence, reasoning, detected_at "
                "FROM fraud_alerts ORDER BY detected_at DESC LIMIT 50"
            )
        except Exception:
            alerts = []

        return {
            "alerts_created": len(
                [t for t in result["tool_calls_made"] if t == "flag_transaction"]
            ),
            "summary": result["content"],
            "alerts": alerts,
        }

    async def _generate_report_section(self, title: str) -> dict:
        try:
            section_prompt = REPORT_SECTION_PROMPTS[title]
            messages = [
                {"role": "system", "content": section_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Write the '{title}' section of an executive report for this "
                        "fintech platform. Investigate the database as needed using your "
                        "tools, then write 2-4 professional paragraphs suitable for a "
                        "C-suite reader. Do not include the section title in your response, "
                        "just the body text."
                    ),
                },
            ]
            result = await self._run_tool_loop(messages, tools=REPORT_TOOL_DEFINITIONS)
            return {"title": title, "content": result["content"]}
        except Exception:
            return {"title": title, "content": "Unable to generate this section."}

    async def generate_report(self) -> dict:
        titles = list(REPORT_SECTION_PROMPTS.keys())
        sections = await asyncio.gather(
            *[self._generate_report_section(title) for title in titles]
        )

        return {
            "sections": list(sections),
            "generated_at": time.time(),
        }

    async def get_commentary(self, metrics_snapshot: dict) -> str:
        now = time.time()
        # Scenario-aware cache TTL: refresh quickly while a scenario is actively
        # running (near-real-time narration during Act 1/2), fall back to a
        # longer cache at idle to control MaaS API cost when nothing's happening.
        ttl = 6 if metrics_snapshot.get("scenario_state", "idle") != "idle" else 25
        if now - self._commentary_ts < ttl and self._cached_commentary:
            return self._cached_commentary

        prompt = (
            f"Current metrics: QPS={metrics_snapshot.get('qps',0)}, "
            f"latency_ms={metrics_snapshot.get('latency_ms',0)}, "
            f"connections={metrics_snapshot.get('connections',0)}, "
            f"slow_queries={metrics_snapshot.get('slow_queries',0)}, "
            f"scenario={metrics_snapshot.get('scenario_state','idle')}, "
            f"scenario_message='{metrics_snapshot.get('scenario_message','')}', "
            f"progress={metrics_snapshot.get('scenario_progress',0):.0f}%"
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": COMMENTARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=80,
                temperature=0.7,
            )
            self._cached_commentary = response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("MaaS commentary generation failed: %s", exc)
        self._commentary_ts = now
        return self._cached_commentary
