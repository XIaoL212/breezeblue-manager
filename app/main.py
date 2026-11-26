"""BreezeBlue account manager: login, daily credits, API-key creation and relay."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import breezeblue_client as breezeblue


DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.environ.get("BREEZEBLUE_DB_PATH", os.path.join(DATA_DIR, "breezeblue_v3.db"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

SITE_ORIGIN = os.environ.get("BREEZEBLUE_SITE_ORIGIN", "https://breezeblue.ai").rstrip("/")
API_BASE_URL = os.environ.get("BREEZEBLUE_API_BASE_URL", "https://api.breeze.blue").rstrip("/")
WEB_BASE_URL = os.environ.get("BREEZEBLUE_SESSION_API_BASE_URL", API_BASE_URL).rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("BREEZEBLUE_TIMEOUT", "90"))
SESSION_TTL = float(os.environ.get("BREEZEBLUE_SESSION_TTL", "86400"))
AUTO_CHECKIN_ENABLED = os.environ.get("BREEZEBLUE_AUTO_CHECKIN", "1").lower() not in ("0", "false", "no", "off")
AUTO_CHECKIN_INTERVAL = float(os.environ.get("BREEZEBLUE_CHECKIN_INTERVAL", "3600"))
DEFAULT_ADMIN = os.environ.get("BREEZEBLUE_ADMIN_USER", "XIaoL")
DEFAULT_ADMIN_PASSWORD = os.environ.get("BREEZEBLUE_ADMIN_PASSWORD", "55588800qq")

try:
    LOCAL_TZ = timezone(timedelta(hours=float(os.environ.get("LOCAL_TZ_OFFSET_HOURS", "8"))))
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=8))


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def now_iso() -> str:
    return now_local().isoformat()


def local_date() -> str:
    return now_local().date().isoformat()


def is_today(value: Any) -> bool:
    return str(value or "").startswith(local_date())


def json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def mask_email(value: str) -> str:
    value = str(value or "")
    if "@" not in value:
        return value
    name, domain = value.split("@", 1)
    if len(name) <= 2:
        return f"{name[:1]}***@{domain}"
    return f"{name[:2]}***@{domain}"


def mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}****{value[-4:]}"


def new_fingerprint() -> dict:
    return {
        "os": "linux",
        "browser": "chrome",
        "browser_version": "131.0.0.0",
        "platform": "Linux",
        "platform_version": "6.8.0",
        "screen_width": 1920,
        "screen_height": 1080,
        "locale": "zh-CN",
        "accept_language": "zh-CN,zh;q=0.9,en;q=0.8",
        "device_id": str(uuid.uuid4())[:24],
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="141", "Not?A_Brand";v="24", "Google Chrome";v="141"',
        "color_depth": 24,
    }


def web_headers(fingerprint: dict) -> dict:
    value = fingerprint if isinstance(fingerprint, dict) else {}
    headers = {
        "User-Agent": str(value.get("user_agent") or new_fingerprint()["user_agent"]),
        "Accept-Language": str(value.get("accept_language") or "zh-CN,zh;q=0.9,en;q=0.8"),
        "Accept": "application/json, text/plain, */*",
        "Origin": SITE_ORIGIN,
        "Referer": f"{SITE_ORIGIN}/auth/login?next=%2Fapp%2Fhome",
        "Content-Type": "application/json",
        "DNT": "1",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if value.get("sec_ch_ua"):
        headers["sec-ch-ua"] = str(value.get("sec_ch_ua"))
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = f'"{value.get("platform") or "Linux"}"'
    return headers


def make_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=3)
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True, transport=transport)


def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_account_row(account_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM accounts WHERE id=?", (int(account_id),)).fetchone()


def get_account_by_email(email: str):
    with db() as conn:
        return conn.execute("SELECT * FROM accounts WHERE lower(email)=?", (str(email or "").strip().lower(),)).fetchone()


def account_public(row, include_catalog: bool = False) -> Optional[dict]:
    if not row:
        return None
    account = json_load(row["account_json"], {})
    if not isinstance(account, dict):
        account = {}
    models = json_load(row["models_json"], [])
    voices = json_load(row["voices_json"], [])
    status = str(row["status"] or "pending_login")
    if not status:
        status = "connected" if row["api_key"] else "pending_login"
    result = {
        "id": int(row["id"]),
        "label": str(row["label"] or f"BreezeBlue #{row['id']}"),
        "email": mask_email(row["email"] or ""),
        "full_email": str(row["email"] or ""),
        "base_url": API_BASE_URL,
        "enabled": bool(row["enabled"]),
        "status": status,
        "plan_code": str(row["plan_code"] or ""),
        "credits": float(row["credits"] or 0),
        "checked_in_today": is_today(row["last_checkin_at"]),
        "has_api_key": bool(row["api_key"]),
        "api_key_masked": mask_secret(row["api_key"] or ""),
        "has_credentials": bool(row["email"] and row["password"]),
        "has_web_session": bool(row["web_session"]),
        "auto_checkin": bool(row["auto_checkin"]),
        "last_checkin_at": str(row["last_checkin_at"] or ""),
        "last_reward": str(row["last_reward"] or ""),
        "last_sync_at": str(row["last_sync_at"] or ""),
        "last_error": str(row["last_error"] or ""),
        "current_key": account.get("current_key") or {},
        "balance": account.get("balance") or {},
        "entitlements": account.get("entitlements") or {},
        "relay_base_url": f"{API_BASE_URL}/v1",
    }
    if include_catalog:
        result["models"] = models
        result["voices"] = voices
        result["model_count"] = len(models if isinstance(models, list) else [])
        result["voice_count"] = len(voices if isinstance(voices, list) else [])
    return result


def add_checkin_log(account_id: int, status: str, credits_earned: float = 0.0, new_total: float = 0.0, error: str = "") -> None:
    with db() as conn:
        conn.execute(
            """INSERT INTO checkin_logs(account_id,ts,credits_earned,new_total,status,error)
               VALUES(?,?,?,?,?,?)""",
            (int(account_id), now_iso(), float(credits_earned or 0), float(new_total or 0), status, str(error or "")[:1000]),
        )


def update_account_error(account_id: int, error: str) -> None:
    with db() as conn:
        conn.execute("UPDATE accounts SET last_error=?,updated_at=? WHERE id=?", (str(error or "")[:1000], now_iso(), int(account_id)))


def extract_amount(*sources: dict) -> float:
    keys = (
        "reward_amount",
        "amount",
        "claimed_amount",
        "credits_earned",
        "daily_claim_amount",
        "rewardAmount",
        "totalClaimed",
        "reward",
        "credits",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source and source.get(key) is not None:
                try:
                    return float(source.get(key) or 0)
                except Exception:
                    continue
    return 0.0


async def breezeblue_web_login(email: str, password: str, fingerprint: dict) -> dict:
    if not email or not password:
        raise HTTPException(400, "BreezeBlue 邮箱和密码不能为空")
    try:
        async with make_client() as client:
            response = await client.post(
                f"{WEB_BASE_URL}/api/auth/email/continue",
                headers=web_headers(fingerprint),
                json={"email": email.strip(), "password": password, "locale": "zh", "next_path": "/app/home"},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"连接 BreezeBlue 登录接口超时：{exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"无法连接 BreezeBlue 登录接口：{exc}") from exc

    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        message = str(payload.get("detail") or payload.get("error") or response.text)[:500]
        status = response.status_code if response.status_code in (400, 401, 403, 422, 429) else 502
        raise HTTPException(status, f"BreezeBlue 登录失败：{message}")
    if str(payload.get("status") or "") != "authenticated":
        raise HTTPException(401, f"BreezeBlue 登录未完成：{str(payload)[:500]}")

    cookie = response.cookies.get("breeze_blue_session") or ""
    if not cookie:
        for header in response.headers.get_list("set-cookie"):
            match = re.search(r"(?:^|;\s*)breeze_blue_session=([^;]+)", header)
            if match:
                cookie = match.group(1)
                break
    if not cookie:
        raise HTTPException(502, "BreezeBlue 登录成功但未返回会话 Cookie")
    return {"cookie": "breeze_blue_session=" + cookie, "user": payload.get("user") or {}}


async def breezeblue_web_request(row, method: str, path: str, json_body: Optional[dict] = None) -> httpx.Response:
    cookie = str(row["web_session"] or "")
    if not cookie:
        raise HTTPException(409, "BreezeBlue 网页会话尚未登录")
    fingerprint = json_load(row["fingerprint_json"], {}) or new_fingerprint()
    headers = web_headers(fingerprint)
    headers["Cookie"] = cookie
    headers["Referer"] = f"{SITE_ORIGIN}/app/developer/api-keys"
    try:
        async with make_client() as client:
            return await client.request(method.upper(), WEB_BASE_URL + path, headers=headers, json=json_body)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"连接 BreezeBlue 接口超时：{exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"无法连接 BreezeBlue 接口：{exc}") from exc


async def breezeblue_sync_account(account_id: int) -> dict:
    row = get_account_row(account_id)
    if not row:
        raise HTTPException(404, "BreezeBlue 账户不存在")
    key = str(row["api_key"] or "")
    if not key:
        raise HTTPException(400, "BreezeBlue API Key 尚未配置")
    try:
        snapshot = await breezeblue.account_snapshot(key, API_BASE_URL)
    except breezeblue.BreezeBlueError as exc:
        update_account_error(account_id, str(exc))
        status = exc.status_code if 400 <= exc.status_code < 600 else 502
        raise HTTPException(status, str(exc)) from exc
    now = now_iso()
    with db() as conn:
        conn.execute(
            """UPDATE accounts SET status=?,plan_code=?,credits=?,account_json=?,models_json=?,
                       voices_json=?,last_sync_at=?,last_error='',updated_at=? WHERE id=?""",
            (
                snapshot["status"],
                snapshot["plan_code"],
                float(snapshot.get("credits") or 0),
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(snapshot.get("models") or [], ensure_ascii=False),
                json.dumps(snapshot.get("voices") or [], ensure_ascii=False),
                now,
                now,
                int(account_id),
            ),
        )
    return account_public(get_account_row(account_id), include_catalog=True)


async def breezeblue_create_key_internal(account_id: int, name: str, credit_limit: Optional[float], create_only_if_missing: bool = False) -> dict:
    row = get_account_row(account_id)
    if not row:
        raise HTTPException(404, "BreezeBlue 账户不存在")
    if create_only_if_missing and row["api_key"]:
        return {"created": False, "api_key": ""}
    response = await breezeblue_web_request(row, "POST", "/v1/api-keys", json_body={"name": str(name or "BreezeBlue Manager")[:120], "credit_limit": credit_limit})
    try:
        payload = response.json()
    except Exception:
        payload = {"detail": response.text[:500]}
    if response.status_code >= 400:
        status = response.status_code if response.status_code < 500 else 502
        raise HTTPException(status, f"BreezeBlue 创建 Key 失败：{str(payload.get('detail') if isinstance(payload, dict) else payload)[:400]}")
    api_key = str(payload.get("api_key") or payload.get("token") or "") if isinstance(payload, dict) else ""
    if not api_key:
        raise HTTPException(502, "BreezeBlue 未返回一次性 API Key")
    with db() as conn:
        conn.execute("UPDATE accounts SET api_key=?,status='connected',last_error='',updated_at=? WHERE id=?", (api_key, now_iso(), int(account_id)))
    public = await breezeblue_sync_account(account_id)
    return {"created": True, "api_key": api_key, "item": public}


async def breezeblue_auto_login(account_id: int, create_key: bool = False) -> dict:
    row = get_account_row(account_id)
    if not row:
        raise HTTPException(404, "BreezeBlue 账户不存在")
    if not row["email"] or not row["password"]:
        raise HTTPException(400, "尚未保存 BreezeBlue 登录邮箱和密码")
    fingerprint = json_load(row["fingerprint_json"], {}) or new_fingerprint()
    session = await breezeblue_web_login(row["email"], row["password"], fingerprint)
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET web_session=?,fingerprint_json=?,last_error='',updated_at=? WHERE id=?",
            (session["cookie"], json.dumps(fingerprint, ensure_ascii=False), now_iso(), int(account_id)),
        )
    if create_key and not get_account_row(account_id)["api_key"]:
        await breezeblue_create_key_internal(account_id, "BreezeBlue Manager", None, create_only_if_missing=True)
    current = get_account_row(account_id)
    if current and current["api_key"]:
        try:
            await breezeblue_sync_account(account_id)
        except Exception:
            pass
    return {"ok": True, "user": session.get("user") or {}, "item": account_public(get_account_row(account_id), include_catalog=True)}


async def breezeblue_claim_daily(account_id: int, force: bool = True) -> dict:
    row = get_account_row(account_id)
    if not row:
        raise HTTPException(404, "BreezeBlue 账户不存在")
    if not force and is_today(row["last_checkin_at"]):
        return {"ok": True, "already_claimed": True, "skipped": True, "item": account_public(row)}
    if not row["web_session"]:
        await breezeblue_auto_login(account_id, create_key=False)
        row = get_account_row(account_id)
    response = await breezeblue_web_request(row, "POST", "/api/credits/daily-claim")
    if response.status_code in (401, 403, 409):
        await breezeblue_auto_login(account_id, create_key=False)
        row = get_account_row(account_id)
        response = await breezeblue_web_request(row, "POST", "/api/credits/daily-claim")
    try:
        payload = response.json()
    except Exception:
        payload = {"detail": response.text[:500]}
    if response.status_code >= 400:
        text = str(payload.get("detail") if isinstance(payload, dict) else payload)[:400]
        status = response.status_code if response.status_code in (401, 403, 422, 429) else 502
        update_account_error(account_id, f"签到失败：{text}")
        raise HTTPException(status, f"BreezeBlue 签到失败：{text}")

    summary_response = await breezeblue_web_request(row, "GET", "/api/credits/summary")
    try:
        summary = summary_response.json()
    except Exception:
        summary = {}
    summary = summary if isinstance(summary, dict) else {}
    already_claimed = bool(payload.get("already_claimed") if isinstance(payload, dict) else False)
    claimed_today = bool(summary.get("daily_claim_claimed_today")) or already_claimed
    balance = float(summary.get("balance") or summary.get("credits") or row["credits"] or 0)
    reward = extract_amount(payload if isinstance(payload, dict) else {}, summary)
    claimed_at = now_iso() if claimed_today or response.status_code < 400 else str(row["last_checkin_at"] or "")
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET credits=?,last_checkin_at=?,last_reward=?,last_error='',updated_at=? WHERE id=?",
            (balance, claimed_at, str(reward or payload if reward else payload), now_iso(), int(account_id)),
        )
    add_checkin_log(account_id, "success", reward, balance, "" if response.status_code < 400 else "already claimed")
    try:
        await breezeblue_sync_account(account_id)
    except Exception:
        pass
    return {
        "ok": True,
        "already_claimed": already_claimed,
        "checked_in_today": is_today(claimed_at),
        "credits": balance,
        "credits_earned": reward,
        "last_checkin_at": claimed_at,
        "result": payload,
        "summary": summary,
        "item": account_public(get_account_row(account_id)),
    }


async def process_new_account(account_id: int) -> None:
    try:
        await breezeblue_auto_login(account_id, create_key=True)
        row = get_account_row(account_id)
        if row and bool(row["auto_checkin"]):
            await breezeblue_claim_daily(account_id, force=True)
    except Exception as exc:
        update_account_error(account_id, str(exc))


async def auto_checkin_accounts() -> int:
    with db() as conn:
        rows = conn.execute("SELECT id,last_checkin_at FROM accounts WHERE enabled=1 AND auto_checkin=1 ORDER BY id").fetchall()
    completed = 0
    for row in rows:
        if is_today(row["last_checkin_at"]):
            continue
        try:
            await breezeblue_claim_daily(int(row["id"]), force=True)
            completed += 1
        except Exception as exc:
            update_account_error(int(row["id"]), str(exc))
    return completed


async def auto_checkin_loop() -> None:
    if not AUTO_CHECKIN_ENABLED:
        return
    await asyncio.sleep(20)
    while True:
        try:
            await auto_checkin_accounts()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print("breezeblue-auto-checkin-failed", repr(exc), flush=True)
        await asyncio.sleep(AUTO_CHECKIN_INTERVAL)


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT DEFAULT '',
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                web_session TEXT DEFAULT '',
                fingerprint_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'pending_login',
                plan_code TEXT DEFAULT '',
                credits REAL DEFAULT 0,
                account_json TEXT DEFAULT '{}',
                models_json TEXT DEFAULT '[]',
                voices_json TEXT DEFAULT '[]',
                auto_checkin INTEGER DEFAULT 1,
                enabled INTEGER DEFAULT 1,
                last_checkin_at TEXT DEFAULT '',
                last_reward TEXT DEFAULT '',
                last_sync_at TEXT DEFAULT '',
                last_used_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                credits_earned REAL DEFAULT 0,
                new_total REAL DEFAULT 0,
                status TEXT NOT NULL,
                error TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_checkin_logs_account_ts ON checkin_logs(account_id, ts);
            """
        )
        pw_hash = hashlib.sha256(DEFAULT_ADMIN_PASSWORD.encode()).hexdigest()
        row = conn.execute("SELECT id FROM admins WHERE username=?", (DEFAULT_ADMIN,)).fetchone()
        if row:
            conn.execute("UPDATE admins SET password_hash=? WHERE username=?", (pw_hash, DEFAULT_ADMIN))
        else:
            conn.execute("INSERT INTO admins(username,password_hash,created_at) VALUES(?,?,?)", (DEFAULT_ADMIN, pw_hash, now_iso()))


class LoginIn(BaseModel):
    username: str
    password: str


class AdminIn(BaseModel):
    username: str
    password: str


class AccountIn(BaseModel):
    email: str
    password: str
    label: str = ""
    auto_checkin: bool = True


class KeyIn(BaseModel):
    name: str = "BreezeBlue Manager"
    credit_limit: Optional[float] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(auto_checkin_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="BreezeBlue Manager", version="3.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.post("/api/admin")
async def create_admin(body: AdminIn):
    with db() as conn:
        conn.execute(
            "INSERT INTO admins(username,password_hash,created_at) VALUES(?,?,?)",
            (body.username, hashlib.sha256(body.password.encode()).hexdigest(), now_iso()),
        )
    return {"ok": True}


@app.post("/api/login")
async def login(body: LoginIn):
    pw_hash = hashlib.sha256(body.password.encode()).hexdigest()
    with db() as conn:
        row = conn.execute("SELECT * FROM admins WHERE username=? AND password_hash=?", (body.username, pw_hash)).fetchone()
        if not row:
            raise HTTPException(401, "用户名或密码错误")
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions(token,username,expires_at) VALUES(?,?,?)",
            (token, body.username, time.time() + SESSION_TTL),
        )
        return {"token": token, "username": body.username}


def get_admin(request: Request) -> str:
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(401, "未登录")
    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token=? AND expires_at>?", (token, time.time())).fetchone()
    if not row:
        raise HTTPException(401, "会话过期")
    return str(row["username"])


@app.post("/api/accounts")
async def add_account(body: AccountIn, admin: str = Depends(get_admin)):
    email = body.email.strip().lower()
    existing = get_account_by_email(email)
    if existing:
        return {"ok": True, "duplicate": True, "item": account_public(existing)}
    now = now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO accounts(email,password,label,auto_checkin,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (email, body.password, body.label.strip()[:120], int(body.auto_checkin), now, now),
        )
    row = get_account_by_email(email)
    asyncio.create_task(process_new_account(int(row["id"])))
    return {"ok": True, "item": account_public(row)}


@app.get("/api/accounts")
async def list_accounts(admin: str = Depends(get_admin)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return [account_public(row) for row in rows]


@app.delete("/api/accounts/{aid}")
async def delete_account(aid: int, admin: str = Depends(get_admin)):
    if not get_account_row(aid):
        raise HTTPException(404, "账户不存在")
    with db() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (int(aid),))
    return {"ok": True}


@app.post("/api/accounts/{aid}/login")
async def login_account(aid: int, admin: str = Depends(get_admin)):
    return await breezeblue_auto_login(aid, create_key=False)


@app.post("/api/accounts/{aid}/sync")
async def sync_account_endpoint(aid: int, admin: str = Depends(get_admin)):
    return {"ok": True, "item": await breezeblue_sync_account(aid)}


@app.post("/api/accounts/{aid}/checkin")
async def checkin_account(aid: int, admin: str = Depends(get_admin)):
    row = get_account_row(aid)
    if not row:
        raise HTTPException(404, "账户不存在")
    return await breezeblue_claim_daily(aid, force=True)


@app.get("/api/accounts/{aid}/keys")
async def list_account_keys(aid: int, admin: str = Depends(get_admin)):
    row = get_account_row(aid)
    if not row:
        raise HTTPException(404, "账户不存在")
    response = await breezeblue_web_request(row, "GET", "/v1/api-keys")
    try:
        payload = response.json()
    except Exception:
        payload = {"detail": response.text[:500]}
    if response.status_code >= 400:
        raise HTTPException(502, f"读取 BreezeBlue Key 失败：{payload}")
    return {"ok": True, "items": payload}


@app.post("/api/accounts/{aid}/keys")
async def create_account_key(aid: int, body: Optional[KeyIn] = None, admin: str = Depends(get_admin)):
    body = body or KeyIn()
    result = await breezeblue_create_key_internal(aid, body.name, body.credit_limit)
    return {"ok": True, **result}


@app.get("/api/logs")
async def get_logs(admin: str = Depends(get_admin)):
    with db() as conn:
        rows = conn.execute(
            """SELECT l.*, a.email AS account_email FROM checkin_logs l
               LEFT JOIN accounts a ON a.id=l.account_id ORDER BY l.id DESC LIMIT 50"""
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["account_email"] = mask_email(item.get("account_email") or "")
        item["success"] = str(item.get("status") or "") == "success"
        item["message"] = str(item.get("error") or ("已领取" if item["success"] else "失败"))
        items.append(item)
    return items


@app.post("/api/checkin/all")
async def checkin_all(admin: str = Depends(get_admin)):
    results = {"success": 0, "failed": 0, "items": []}
    with db() as conn:
        rows = conn.execute("SELECT id,email FROM accounts WHERE enabled=1 ORDER BY id").fetchall()
    for row in rows:
        try:
            res = await breezeblue_claim_daily(int(row["id"]), force=True)
            results["success"] += 1
            results["items"].append({"id": int(row["id"]), "email": mask_email(row["email"]), "ok": True, "already_claimed": bool(res.get("already_claimed"))})
        except Exception as exc:
            results["failed"] += 1
            results["items"].append({"id": int(row["id"]), "email": mask_email(row["email"]), "ok": False, "error": str(exc)[:200]})
    return results


@app.get("/api/stats")
async def get_stats(admin: str = Depends(get_admin)):
    today = local_date()
    with db() as conn:
        account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        total_credits = conn.execute("SELECT COALESCE(SUM(credits),0) FROM accounts").fetchone()[0]
        today_checkins = conn.execute("SELECT COUNT(*) FROM checkin_logs WHERE status='success' AND ts LIKE ?", (f"{today}%",)).fetchone()[0]
    return {"account_count": int(account_count), "total_credits": float(total_credits), "today_checkins": int(today_checkins)}


def resolve_relay_account(request: Request):
    value = request.headers.get("X-BreezeBlue-Account") or request.query_params.get("account_id") or ""
    if value:
        row = get_account_row(int(value))
        if not row:
            raise HTTPException(404, "BreezeBlue 账户不存在")
        return row
    with db() as conn:
        rows = conn.execute("SELECT id FROM accounts WHERE enabled=1 ORDER BY id LIMIT 2").fetchall()
    if len(rows) == 1:
        return get_account_row(int(rows[0]["id"]))
    raise HTTPException(400, "多个账户时必须指定 account_id")


@app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def relay_v1(rest: str, request: Request, admin: str = Depends(get_admin)):
    row = resolve_relay_account(request)
    if not row["api_key"]:
        try:
            await breezeblue_auto_login(int(row["id"]), create_key=True)
        except Exception as exc:
            return JSONResponse({"detail": f"账户尚未生成可用 API Key：{exc}"}, status_code=409)
    row = get_account_row(int(row["id"]))
    if not row or not row["api_key"]:
        raise HTTPException(409, "BreezeBlue API Key 尚未生成")
    body = await request.body()
    upstream_headers = {
        "xi-api-key": str(row["api_key"]),
        "Authorization": f"Bearer {str(row['api_key'])}",
    }
    if request.headers.get("content-type"):
        upstream_headers["Content-Type"] = request.headers.get("content-type")
    query = [(k, v) for k, v in request.query_params.multi_items() if k != "account_id"]
    client = make_client()
    try:
        req = client.build_request(request.method, API_BASE_URL + request.url.path, headers=upstream_headers, params=query, content=body)
        upstream = await client.send(req, stream=True)
    except breezeblue.BreezeBlueError as exc:
        await client.aclose()
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code or 502)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse({"detail": f"无法连接 BreezeBlue：{exc}"}, status_code=502)

    hop = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "date", "server"}
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in hop}

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    with db() as conn:
        conn.execute("UPDATE accounts SET last_used_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), int(row["id"])))
    return StreamingResponse(stream_body(), status_code=upstream.status_code, headers=response_headers, media_type=upstream.headers.get("content-type", "application/json"))


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
