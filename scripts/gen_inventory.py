#!/usr/bin/env python3
"""Bridge terraform outputs → .env + ansible/inventory.ini.

Run after `terraform apply`:
    python scripts/gen_inventory.py

Reads `terraform output -json` and patches:
  - .env  (TAURUS_HOST, TAURUS_PORT, TAURUS_INSTANCE_ID, DEMO_RUNNER_IP)
  - ansible/inventory.ini
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
INVENTORY_FILE = ROOT / "ansible" / "inventory.ini"


def get_tf_outputs() -> dict:
    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=str(ROOT / "terraform"),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"terraform output failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def patch_env(outputs: dict) -> None:
    mapping = {
        "taurus_host": "TAURUS_HOST",
        "taurus_port": "TAURUS_PORT",
        "taurus_instance_id": "TAURUS_INSTANCE_ID",
        "demo_runner_public_ip": "DEMO_RUNNER_IP",
    }

    if not ENV_FILE.exists():
        print(f".env not found at {ENV_FILE}", file=sys.stderr)
        sys.exit(1)

    lines = ENV_FILE.read_text().splitlines()
    updated = set()

    for tf_key, env_key in mapping.items():
        if tf_key not in outputs:
            continue
        value = outputs[tf_key].get("value", "")
        if isinstance(value, list):
            value = ",".join(str(v) for v in value)
        else:
            value = str(value)

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{env_key}=") or stripped == f"{env_key}=":
                lines[i] = f"{env_key}={value}"
                updated.add(env_key)
                break

    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"Patched .env: {', '.join(sorted(updated)) or 'no changes'}")


def write_inventory(outputs: dict) -> None:
    runner_ip = outputs.get("demo_runner_public_ip", {}).get("value", "")
    if not runner_ip:
        print(
            "demo_runner_ip not in terraform outputs, skipping inventory",
            file=sys.stderr,
        )
        return

    INVENTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    taurus_hosts = outputs.get("taurus_host", {}).get("value", "")
    if isinstance(taurus_hosts, list):
        taurus_hosts = taurus_hosts[0] if taurus_hosts else ""

    content = f"""[demo_runner]
runner ansible_host={runner_ip} ansible_user=root ansible_ssh_private_key_file={outputs.get('ssh_key_path', {}).get('value', '~/.ssh/taurus_demo_key')}

[taurus]
taurus_db ansible_host={taurus_hosts}

[all:children]
demo_runner
taurus
"""
    INVENTORY_FILE.write_text(content)
    print(f"Wrote {INVENTORY_FILE}")


def main() -> None:
    outputs = get_tf_outputs()
    patch_env(outputs)
    write_inventory(outputs)


if __name__ == "__main__":
    main()
