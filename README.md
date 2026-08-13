# TaurusDB + MaaS AI — Fintech Demo

FastAPI + WebSocket dashboard driving a scripted ~20-min demo: bulk workload → HA failover → AI fraud analysis, against a real TaurusDB (GaussDB for MySQL) HA instance and GLM-5.1 via MaaS AI.

## Quick start

```bash
git clone git@github.com:araujoviana/taurus-maas-pratical.git
cd taurus-maas-pratical
uv sync
make hooks    # secret-scanning pre-commit/pre-push (once per clone)

cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # HW AK/SK, project ID, SSH key path
cp .env.example .env                                                # HW_*, MAAS_API_KEY, DEMO_PASSWORD

make up       # terraform apply -> gen_inventory.py -> ansible-playbook, ~15 min
```

Prints `Dashboard: http://<demo_runner_public_ip>` on completion. Login `admin` / `$DEMO_PASSWORD`.

Requires: `uv`, `terraform` >= 1.5, `ansible` >= 2.15, a Huawei Cloud account with ECS + GaussDB MySQL + MaaS AI access, an SSH keypair.

## Local dev (no cloud infra)

```bash
uv run python main.py     # http://localhost:8000 — DB connect fails silently, UI/WS still serve
uv run pytest tests/ -v
```

## Running the demo

Each act is a POST from the dashboard buttons; state lives in `ScenarioManager` (`dashboard/scenarios.py`), which enforces one scenario at a time.

| Route | Handler | Does |
|---|---|---|
| `POST /scenario/start-load` | `scenarios/workload.py` | Faker bulk insert (200 accounts, 10k txns) — QPS spike on the dashboard |
| `POST /scenario/kill-primary` | `scenarios/failover.py` | GaussDB MySQL switchover REST API call, polls `SELECT 1` until recovered (<30s) |
| `POST /scenario/ai-analyze` | `scenarios/ai_analytics.py` | One-shot GLM-5.1 fraud pass over recent + sampled transactions; escalates `risk_score >= 7` to 10 |
| `POST /scenario/reset` | — | Clears `ScenarioManager` state back to `IDLE` |
| `POST /fraud/inject/{pattern}` | `scenarios/fraud_injection.py` | Synthesizes obviously-fraudulent rows (`velocity` \| `large_transfer` \| `geo_anomaly`) for detection to find — not AI-driven |
| `POST /fraud/analyze` | `dashboard/ai_engine.py` | Tool-calling agent hunts + flags fraud over the last 30 min |
| `POST /ai/chat` | `dashboard/ai_engine.py` | Conversational SQL Q&A with chart generation (chat panel) |
| `GET /ai/report` | `dashboard/ai_engine.py` | Executive report from six canned aggregate queries |
| `GET /ai/commentary` | `dashboard/ai_engine.py` | 25s-cached one-liner for the dashboard ticker |
| `GET /db/stats`, `/accounts`, `/transactions`, `/scenario/status`, `/fraud/alerts` | — | Read-only polling endpoints backing the UI |

The AI tool-calling agent (`MaaSClient`) exposes 4 tools to the model: `run_sql` (SELECT-only, `_validate_sql` guarded), `get_db_metrics`, `flag_transaction`, `get_account_details`.

## Environment variables

Full list in `.env.example`. You supply manually:

| Variable | Used for |
|---|---|
| `HW_ACCESS_KEY`, `HW_SECRET_KEY`, `HW_PROJECT_ID` | AKSK signing for the GaussDB switchover API |
| `DEMO_PASSWORD` | TaurusDB `demouser` password + JWT secret seed (HS256, 8h TTL) |
| `MAAS_API_KEY`, `MAAS_BASE_URL`, `MAAS_MODEL` | GLM-5.1 via `openai.OpenAI`/`AsyncOpenAI` clients |
| `SSH_KEY_PATH` | Ansible's private key for the ECS host (pairs with `ssh_public_key_path` in `terraform.tfvars`) |

Auto-filled by `scripts/gen_inventory.py` after `terraform apply` — don't hand-edit, overwritten on every `make up`:

| Variable | Source |
|---|---|
| `TAURUS_HOST` | GaussDB MySQL proxy endpoint |
| `TAURUS_INSTANCE_ID` | Needed for the failover API call |
| `DEMO_RUNNER_IP` | ECS public IP |

## Make targets

```bash
make up      # terraform apply + gen_inventory.py + ansible-playbook
make seed    # re-seed: 10k accounts + 500k transactions (scripts/seed_data.py)
make logs    # journalctl -u taurus-demo -f, via ansible
make down    # terraform destroy
make hooks   # point core.hooksPath at .githooks/ (secret scanning)
```

`scripts/bulk_populate.py` adds 40k accounts + 4.5M transactions on top of an existing seed — run manually, not wired into `make`/ansible.

## Project structure

```
main.py                      # local dev entry (uvicorn --reload)

dashboard/
  main.py                    # FastAPI app: routes above, WebSocket /ws
  collectors.py               # TaurusDBCollector (QPS delta, latency) + MAASCollector (30s cache)
  scenarios.py                 # ScenarioManager state machine
  database.py                  # aiomysql pool + schema
  auth.py                      # JWT create/verify
  ai_engine.py                  # MaaSClient — tool-calling agent (chat/report/commentary/fraud)
  static/                       # index.html + app.js (ApexCharts) + style.css

scenarios/                     # workload.py, failover.py, ai_analytics.py, fraud_injection.py
scripts/                       # gen_inventory.py, seed_data.py, bulk_populate.py
terraform/                     # VPC, ECS, EIP, TaurusDB HA instance + proxy
ansible/                       # site.yml (setup, deploy+seed, verify) + templates/
tests/                         # one file per dashboard module
```

Full architecture notes (metrics pipeline, failover mechanism, asyncio executor gotchas, live ECS deployment reference) are in `CLAUDE.md`.

## Security notes

- `.env` and `terraform/terraform.tfvars` are gitignored — never commit them.
- `make hooks` installs pre-commit/pre-push hooks (`.githooks/`) that block commits/pushes containing credential-shaped filenames or content (private keys, AK/SK-style keys, hardcoded passwords), including force-added files. Defense-in-depth on top of `.gitignore`.
- App auth is JWT-only (login overlay, `sessionStorage` token) — there is no nginx basic auth in front of it.
- ECS root password auth is fine for a demo box; use SSH keys + disabled password login for anything longer-lived.
