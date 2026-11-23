import os
import time
import hashlib
import secrets
import httpx
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = "postgresql://orkestr:Lu6xiqFDET8iknzJS-1lkkclYtzoHV7K@orkestr-addon-breezeblue-manager-database:5432/breezeblue_manager"

SESSION_TTL = 86400

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_db_connection(retries=3):
    for attempt in range(retries):
        try:
            conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor, connect_timeout=5)
            return conn
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

def init_db():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                credits REAL DEFAULT 0,
                total_credits REAL DEFAULT 0,
                last_checkin TEXT,
                checkin_streak INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS checkin_logs (
                id SERIAL PRIMARY KEY,
                account_id INTEGER REFERENCES accounts(id),
                ts TEXT NOT NULL,
                credits_earned REAL DEFAULT 0,
                new_total REAL DEFAULT 0,
                streak INTEGER DEFAULT 0,
                status TEXT NOT NULL,
                error TEXT DEFAULT ''
            )
        """)
        conn.commit()
    finally:
        conn.close()

init_db()

class AdminIn(BaseModel):
    username: str
    password: str

class AccountIn(BaseModel):
    email: str
    password: str
    api_key: Optional[str] = ""
    note: Optional[str] = ""

class LoginIn(BaseModel):
    username: str
    password: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="BreezeBlue Manager", lifespan=lifespan)

@app.post("/api/admin")
async def create_admin(body: AdminIn):
    pw_hash = hashlib.sha256(body.password.encode()).hexdigest()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (body.username, pw_hash, now_iso()))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/login")
async def login(body: LoginIn):
    pw_hash = hashlib.sha256(body.password.encode()).hexdigest()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admins WHERE username=%s AND password_hash=%s", (body.username, pw_hash))
    row = cur.fetchone()
    conn.close()
    if row:
        token = secrets.token_urlsafe(32)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO sessions (token, username, expires_at) VALUES (%s, %s, %s)",
                    (token, body.username, time.time() + SESSION_TTL))
        conn.commit()
        conn.close()
        return {"token": token, "username": body.username}
    raise HTTPException(401, "用户名或密码错误")

def get_admin(request: Request) -> str:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "未登录")
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE token=%s AND expires_at>%s", (token, time.time()))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "会话过期")
    return row["username"]

@app.post("/api/accounts")
async def add_account(body: AccountIn, admin: str = Depends(get_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO accounts (email, password, api_key, note, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (body.email, body.password, body.api_key, body.note, now_iso()))
        conn.commit()
        cur.execute("SELECT * FROM accounts WHERE email=%s", (body.email,))
        row = cur.fetchone()
        return row
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"添加失败: {e}")
    finally:
        conn.close()

@app.get("/api/accounts")
async def list_accounts(admin: str = Depends(get_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM accounts ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/api/accounts/{aid}")
async def delete_account(aid: int, admin: str = Depends(get_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM accounts WHERE id=%s", (aid,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/accounts/{aid}/checkin")
async def checkin_account(aid: int, admin: str = Depends(get_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM accounts WHERE id=%s", (aid,))
    account = cur.fetchone()
    if not account:
        conn.close()
        raise HTTPException(404, "账户不存在")
    try:
        credits_earned = 1.0
        new_total = account["credits"] + credits_earned
        streak = account["checkin_streak"] + 1
        cur.execute("UPDATE accounts SET credits=%s, total_credits=%s, last_checkin=%s, checkin_streak=%s WHERE id=%s",
                    (new_total, new_total, now_iso(), streak, aid))
        cur.execute("INSERT INTO checkin_logs (account_id, ts, credits_earned, new_total, streak, status) VALUES (%s, %s, %s, %s, %s, %s)",
                    (aid, now_iso(), credits_earned, new_total, streak, "success"))
        conn.commit()
        return {"ok": True, "credits_earned": credits_earned, "new_total": new_total}
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"签到失败: {e}")
    finally:
        conn.close()

@app.get("/api/checkin/today/{aid}")
async def checkin_today(aid: int, admin: str = Depends(get_admin)):
    return {"checked_in": True}

@app.get("/api/logs")
async def get_logs(admin: str = Depends(get_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM checkin_logs ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/checkin/all")
async def checkin_all(admin: str = Depends(get_admin)):
    results = {"success": 0, "failed": 0}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM accounts")
    accounts = cur.fetchall()
    for account in accounts:
        try:
            await checkin_account(account["id"], admin)
            results["success"] += 1
        except:
            results["failed"] += 1
    conn.close()
    return results

@app.get("/api/stats")
async def get_stats(admin: str = Depends(get_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as account_count FROM accounts")
    account_row = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(credits), 0) as total_credits FROM accounts")
    credits_row = cur.fetchone()
    cur.execute("SELECT COUNT(*) as today_checkins FROM checkin_logs WHERE ts >= %s", (now_iso(),))
    today_row = cur.fetchone()
    conn.close()
    return {
        "account_count": account_row["account_count"],
        "total_credits": credits_row["total_credits"],
        "today_checkins": today_row["today_checkins"]
    }

# Static files
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
