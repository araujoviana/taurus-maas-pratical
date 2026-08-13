# TaurusDB + MaaS AI — Fintech Demo Dashboard

A live demo environment showcasing **Huawei Cloud GaussDB for MySQL (TaurusDB)** integrated with **MaaS AI (GLM-5.1)** in a fintech scenario. Designed as a ~20-minute enterprise presentation across three acts.

---

## Demo Overview

| Act | Scenario | What it shows |
|-----|----------|---------------|
| **Act 1 — Performance Storm** | Bulk-load 500k transactions | Live QPS/latency spike on the dashboard |
| **Act 2 — HA Resilience** | Trigger a primary→standby switchover | Sub-30s automatic failover through TaurusDB proxy |
| **Act 3 — MaaS AI Integration** | GLM-5.1 fraud pattern analysis | AI-driven risk scoring against live fintech data |

The dashboard runs on **FastAPI + WebSocket** (port 8000), served via nginx reverse proxy. No local port-forwarding needed — the presenter opens a browser to the ECS public IP.

---

## Architecture

```
Browser
  │  HTTP/WebSocket
  ▼
nginx (port 80)
  │  reverse proxy
  ▼
FastAPI app (port 8000)
  ├── /ws  ─────────────────► TaurusDBCollector (SHOW GLOBAL STATUS + SELECT 1 latency)
  │                          MAASCollector      (GLM-5.1 ping, 30s cache)
  ├── /scenario/start-load ──► workload.py      (Faker bulk insert)
  ├── /scenario/kill-primary ► failover.py      (Huawei Cloud GaussDB switchover API)
  ├── /scenario/ai-analyze ──► ai_analytics.py  (GLM-5.1 fraud analysis)
  └── /auth/login ───────────► auth.py          (JWT HS256, 8h TTL)
        │
        ▼
  TaurusDB Proxy ──► Primary / Standby (GaussDB MySQL HA)
```

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| [uv](https://docs.astral.sh/uv/) | latest | Python package manager |
| [Terraform](https://developer.hashicorp.com/terraform/install) | >= 1.5 | Infrastructure provisioning |
| [Ansible](https://docs.ansible.com/ansible/latest/installation_guide/) | >= 2.15 | App deployment |
| Huawei Cloud account | — | ECS + GaussDB MySQL + MaaS AI access |

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/araujoviana/taurus-maas-pratical.git
cd taurus-maas-pratical
uv sync
make hooks   # enable pre-commit/pre-push secret-scanning (see Security Notes)
```

### 2. Configure credentials

```bash
# Terraform variables
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform/terraform.tfvars — fill in your Huawei Cloud AK, SK, project ID, SSH key path

# App environment
cp .env.example .env
# Edit .env — fill in HW_ACCESS_KEY, HW_SECRET_KEY, HW_PROJECT_ID, MAAS_API_KEY, DEMO_PASSWORD
```

### 3. Provision infrastructure and deploy

```bash
make up
```

This runs three steps automatically:
1. `terraform apply` — creates VPC, ECS, EIP, TaurusDB HA instance + proxy
2. `scripts/gen_inventory.py` — bridges Terraform outputs into `ansible/inventory.ini` and patches `.env`
3. `ansible-playbook` — installs system deps, deploys the app, seeds the database

Takes ~15 minutes on first run.

### 4. Open the dashboard

After `make up` completes, the URL is printed:

```
Dashboard: http://<YOUR_ECS_PUBLIC_IP>
Login: admin / <YOUR_DEMO_PASSWORD>
```

---

## Environment Variables

See [`.env.example`](.env.example) for the full list. Key variables:

| Variable | Description | Source |
|----------|-------------|--------|
| `HW_ACCESS_KEY` | Huawei Cloud Access Key | Manual |
| `HW_SECRET_KEY` | Huawei Cloud Secret Key | Manual |
| `HW_PROJECT_ID` | Huawei Cloud Project ID | Manual |
| `DEMO_PASSWORD` | TaurusDB password + JWT secret seed | Manual |
| `MAAS_API_KEY` | MaaS AI (GLM-5.1) API key | Manual |
| `MAAS_BASE_URL` | MaaS AI endpoint URL | Manual |
| `TAURUS_HOST` | TaurusDB proxy private IP | Auto-filled by `gen_inventory.py` |
| `TAURUS_INSTANCE_ID` | GaussDB instance ID (for failover API) | Auto-filled by `gen_inventory.py` |
| `DEMO_RUNNER_IP` | ECS public IP | Auto-filled by `gen_inventory.py` |

> Variables marked **Auto-filled** are overwritten on every `make up`. Do not edit them manually.

---

## Local Development

Run the app locally without any cloud infrastructure. The DB connection will fail, but the app starts and the WebSocket/UI work:

```bash
uv run python main.py
# Dashboard at http://localhost:8000
```

Run tests:

```bash
uv run pytest tests/ -v
```

---

## Other Make Targets

```bash
make seed   # Re-seed the database (10k accounts + 500k transactions)
make logs   # Stream live app logs from ECS
make down   # Tear down all Terraform-managed infrastructure
```

---

## Project Structure

```
main.py                     # Local dev entry point (uvicorn with reload)
Makefile                    # Deployment lifecycle: up / down / seed / logs
pyproject.toml              # uv-managed dependencies

dashboard/
  main.py                   # FastAPI app — WebSocket, scenario routes, auth
  collectors.py             # TaurusDBCollector + MAASCollector (metrics)
  scenarios.py              # ScenarioManager async state machine
  database.py               # aiomysql connection pool + schema helpers
  auth.py                   # JWT creation/verification
  static/
    index.html              # Dark fintech dashboard (ApexCharts)
    app.js                  # WebSocket client + chart updates
    style.css               # Dark enterprise theme

scenarios/
  workload.py               # Faker-based bulk insert (Act 1)
  failover.py               # GaussDB MySQL switchover via Huawei Cloud API (Act 2)
  ai_analytics.py           # GLM-5.1 fraud pattern analysis (Act 3)

scripts/
  gen_inventory.py          # Bridges terraform output → inventory.ini + .env
  seed_data.py              # CLI: full 10k accounts + 500k transactions seed

terraform/
  main.tf                   # VPC, ECS, EIP, TaurusDB HA instance + proxy
  variables.tf              # All configurable inputs
  outputs.tf                # Exported: taurus_host, taurus_port, demo_runner_public_ip
  terraform.tfvars.example  # Template — copy to terraform.tfvars and fill in

ansible/
  site.yml                  # 3 plays: system setup → app deploy → verify
  inventory.ini             # Auto-generated by gen_inventory.py (gitignored)
  templates/
    taurus-demo.service.j2  # systemd unit (uvicorn on port 8000)
    nginx.conf.j2           # Reverse proxy + WebSocket upgrade

tests/
  test_collectors.py        # TaurusDBCollector QPS rate + MAASCollector cache
  test_scenarios.py         # ScenarioManager state machine transitions
  test_auth.py              # JWT create + verify
```

---

## Pre-Demo Checklist

- [ ] `terraform/terraform.tfvars` created from `.example` with real credentials
- [ ] `.env` has `HW_ACCESS_KEY`, `HW_SECRET_KEY`, `HW_PROJECT_ID`, `MAAS_API_KEY` filled
- [ ] SSH key pair present: public key path in `terraform.tfvars`, private key at `SSH_KEY_PATH`
- [ ] `make up` completed successfully (~15 min)
- [ ] Dashboard accessible at `http://<ECS_IP>` → login works
- [ ] Browser devtools → WebSocket frames arriving every ~1s
- [ ] All 3 acts rehearsed end-to-end

---

## Security Notes

- **Never commit `.env` or `terraform/terraform.tfvars`** — both are gitignored.
- `DEMO_PASSWORD` is used as the TaurusDB password, the nginx admin password, and the JWT secret seed. Use a strong value in production.
- The ECS root password auth is convenient for demos; for real deployments use SSH key auth and disable password login.
- **Secret-scanning git hooks**: run `make hooks` once per clone to enable `pre-commit`/`pre-push` hooks (in `.githooks/`) that block commits/pushes containing credential-shaped filenames (`.env`, `*.pem`, `id_rsa`, `terraform.tfvars`, …) or content (private keys, AWS-style access keys, hardcoded passwords/tokens). This is defense-in-depth on top of `.gitignore` — it also catches force-added (`git add -f`) files.
