from __future__ import annotations

"""Act 2 — TaurusDB HA failover.

Calls the Huawei Cloud GaussDB for MySQL switchover REST API and then polls
the database until it accepts connections again (proxy keeps the endpoint
stable; only a brief QPS dip is visible on the dashboard).

API reference:
  POST /v3/{project_id}/instances/{instance_id}/failover/switchover
  https://support.huaweicloud.com/en-us/api-gaussdbformysql/gaussdbformysql_04_0088.html
"""

import time
from typing import Any

POLL_INTERVAL = 2  # seconds between reconnection attempts
MAX_WAIT = 120  # maximum seconds to wait for recovery


def run_failover(env: dict[str, str]) -> None:
    """Trigger a GaussDB MySQL primary → standby switchover via HW Cloud API.

    Requires env vars:
      HW_ACCESS_KEY, HW_SECRET_KEY, HW_REGION, HW_PROJECT_ID,
      TAURUS_INSTANCE_ID
    """
    import requests
    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkcore.http.http_config import HttpConfig
    from huaweicloudsdkcore.sdk_request import SdkRequest
    from huaweicloudsdkcore.auth.signer import Signer

    ak = env.get("HW_ACCESS_KEY", "")
    sk = env.get("HW_SECRET_KEY", "")
    region = env.get("HW_REGION", "ap-southeast-1")
    project_id = env.get("HW_PROJECT_ID", "")
    instance_id = env.get("TAURUS_INSTANCE_ID", "")

    if not all([ak, sk, project_id, instance_id]):
        raise RuntimeError(
            "HW_ACCESS_KEY, HW_SECRET_KEY, HW_PROJECT_ID, and TAURUS_INSTANCE_ID "
            "must all be set to trigger a real failover."
        )

    url = (
        f"https://gaussdb-mysql.{region}.myhuaweicloud.com"
        f"/v3/{project_id}/instances/{instance_id}/failover/switchover"
    )

    # Sign the request with AKSK
    credentials = BasicCredentials(ak, sk, project_id)
    config = HttpConfig.get_default_config()

    sdk_req = SdkRequest(
        method="POST",
        schema="https",
        host=f"gaussdb-mysql.{region}.myhuaweicloud.com",
        resource_path=f"/v3/{project_id}/instances/{instance_id}/failover/switchover",
        query_params=[],
        header_params={"Content-Type": "application/json"},
        body="",
        stream=False,
    )
    credentials.process_auth_params(sdk_req, region)

    headers = dict(sdk_req.header_params)
    resp = requests.post(url, headers=headers, json={}, timeout=30)

    if resp.status_code not in (200, 201, 202, 204):
        raise RuntimeError(
            f"Switchover API returned {resp.status_code}: {resp.text[:200]}"
        )


def wait_recovery(db: Any, env: dict[str, str]) -> None:
    """Poll the database until it responds (proxy should restore connectivity).

    Runs synchronously — call via run_in_executor from async code.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    start = time.time()
    while time.time() - start < MAX_WAIT:
        try:
            loop.run_until_complete(db.fetchone("SELECT 1"))
            return  # DB is back
        except Exception:
            time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"TaurusDB did not recover within {MAX_WAIT}s after failover."
    )
