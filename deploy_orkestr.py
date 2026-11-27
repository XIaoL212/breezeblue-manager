"""Back up the live container, then deploy master, then wait for it to go live.

Order matters: the running container holds the newest copy of the database until
the new one boots, so the snapshot is taken before the new image starts.

    python deploy_orkestr.py                 # backup + deploy + wait
    python deploy_orkestr.py --backup-only   # snapshot the live container only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("ORKESTR_TOKEN", "ork_uP2NDpQy0gacuiDg3F8u_8MZrftY4W36YCfCbYtVbFQ")
PROJECT_ID = os.environ.get("ORKESTR_PROJECT_ID", "0cbedc47-bc89-4c08-aee8-243404451480")
ENVIRONMENT_ID = os.environ.get("ORKESTR_ENVIRONMENT_ID", "2f3d4b03-efec-4403-8def-3028e8866a71")
API = "https://api.orkestr.eu/api/v1"
APP_URL = os.environ.get("APP_URL", "https://breezeblue-manager.orkestr.run")
ADMIN_USER = os.environ.get("BREEZEBLUE_ADMIN_USER", "XIaoL")
ADMIN_PASSWORD = os.environ.get("BREEZEBLUE_ADMIN_PASSWORD", "55588800qq")


def call(method: str, url: str, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    # method= is required: urllib infers GET whenever the body is empty.
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]


def orkestr(method: str, path: str, body=None, params: str = ""):
    return call(method, f"{API}{path}{params}", body, {"Authorization": f"Bearer {TOKEN}"})


def admin_headers(retries: int = 6) -> dict:
    """Log in to the running app, waiting out a scale-to-wake cold start."""
    last = ""
    for attempt in range(retries):
        status, data = call("POST", f"{APP_URL}/api/login", {"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        if status == 200:
            return {"Authorization": f"Bearer {data['token']}"}
        last = f"HTTP {status}"
        if status == 404:
            break
        print(f"  后台暂不可用（{last}），{attempt + 1}/{retries} 重试 ...", flush=True)
        time.sleep(5)
    raise RuntimeError(f"后台登录失败 {last}")


def snapshot_live() -> None:
    print("备份当前线上容器 ...")
    try:
        status, data = call("POST", f"{APP_URL}/api/backup/run", {}, admin_headers())
    except RuntimeError as exc:
        print(f"  {exc}，跳过备份继续部署")
        return
    result = (data or {}).get("result", {}) if isinstance(data, dict) else {}
    if status == 200 and result.get("ok"):
        print(f"  已快照 {result.get('key')}（{int(result.get('bytes', 0) / 1024):.1f} KB，清理 {result.get('pruned')} 份旧快照）")
    else:
        print(f"  备份未成功（继续部署，新容器会从最近快照或种子账户回灌）：{str(data)[:160]}")


def wait_live(deploy_id: str, timeout: int = 420) -> None:
    """Poll the deployment we created by id; its commit is only filled in later."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, data = orkestr("GET", f"/projects/{PROJECT_ID}/deployments", params="?limit=1")
        dep = (data.get("deployments") or [{}])[0] if status == 200 and isinstance(data, dict) else {}
        if dep.get("id") != deploy_id:
            print("  waiting for the new deployment to appear ...", flush=True)
            time.sleep(5)
            continue
        state = dep.get("status", "unknown")
        print(f"  {state} @ {(dep.get('commit_sha') or '-')[:7]}", flush=True)
        if state == "live":
            return
        if state in {"failed", "error"}:
            steps = [f"{s['step_name']}: {str(s.get('log_output'))[:200]}" for s in dep.get("steps", []) if s.get("status") == "failed"]
            raise SystemExit(f"部署失败：{dep.get('error_message')}\n" + "\n".join(steps))
        time.sleep(6)
    raise SystemExit(f"部署 {deploy_id[:8]} 在 {timeout}s 内没有完成")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-only", action="store_true", help="snapshot the live container and stop")
    parser.add_argument("--no-backup", action="store_true", help="deploy without the pre-deploy snapshot")
    args = parser.parse_args()

    if not args.no_backup:
        snapshot_live()
    if args.backup_only:
        return

    status, data = orkestr("POST", f"/projects/{PROJECT_ID}/deployments", None, f"?environment_id={ENVIRONMENT_ID}")
    if status not in (200, 201):
        raise SystemExit(f"触发部署失败 HTTP {status}: {data}")
    print(f"部署已触发 id={str(data.get('id'))[:8]} status={data.get('status')}")
    wait_live(str(data.get("id") or ""))
    print(f"线上已是新版本：{APP_URL}")


if __name__ == "__main__":
    sys.exit(main())
