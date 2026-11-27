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
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]


def orkestr(method: str, path: str, body=None, params: str = ""):
    return call(method, f"{API}{path}{params}", body, {"Authorization": f"Bearer {TOKEN}"})


def admin_headers() -> dict:
    status, data = call("POST", f"{APP_URL}/api/login", {"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    if status != 200:
        raise SystemExit(f"后台登录失败 HTTP {status}: {data}")
    return {"Authorization": f"Bearer {data['token']}"}


def snapshot_live() -> None:
    print("备份当前线上容器 ...")
    status, data = call("POST", f"{APP_URL}/api/backup/run", {}, admin_headers())
    result = (data or {}).get("result", {}) if isinstance(data, dict) else {}
    if status == 200 and result.get("ok"):
        print(f"  已快照 {result.get('key')}（{int(result.get('bytes', 0) / 1024):.1f} KB，清理 {result.get('pruned')} 份旧快照）")
    else:
        print(f"  备份未成功（继续部署，启动时仍会从最近快照回灌）：{str(data)[:200]}")


def wait_live(commit: str) -> None:
    while True:
        status, data = orkestr("GET", f"/projects/{PROJECT_ID}/deployments", params="?limit=1")
        dep = data["deployments"][0] if status == 200 and data.get("deployments") else {}
        state = dep.get("status", "unknown")
        short = (dep.get("commit_sha") or "")[:7]
        print(f"  {state} @ {short}", flush=True)
        if state == "live":
            if commit and short != commit[:7]:
                raise SystemExit(f"线上是 {short}，不是预期的 {commit[:7]}")
            return
        if state in {"failed", "error"}:
            steps = [f"{s['step_name']}: {str(s.get('log_output'))[:200]}" for s in dep.get("steps", []) if s.get("status") == "failed"]
            raise SystemExit(f"部署失败：{dep.get('error_message')}\n" + "\n".join(steps))
        time.sleep(6)


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
    print(f"部署已触发 @ {(data.get('commit_sha') or '')[:7]} {(data.get('commit_message') or '').strip()}")
    wait_live(data.get("commit_sha") or "")
    print(f"线上已是新版本：{APP_URL}")


if __name__ == "__main__":
    sys.exit(main())
