"""BreezeBlue 批量账户管理与签到系统"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "app" / "static"
DB_PATH = DATA_DIR / "breezeblue.db"
SECRET_PATH = DATA_DIR / "secret.key"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

SESSION_TTL = 12 * 3600


def get_secret() -> bytes:
     if SECRET_PATH.exists():
         return SECRET_PATH.read_bytes()
     key = secrets.token_bytes(32)
     SECRET_PATH.write_bytes(key)
     return key


SECRET = get_secret()


def db() -> sqlite3.Connection:
     conn = sqlite3.connect(DB_PATH, timeout=20)
     conn.row_factory = sqlite3.Row
     conn.execute("PRAGMA journal_mode=WAL")
     return conn


def now_iso() -> str:
     return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
     with db() as conn:
         conn.executescript("""
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
             );
             CREATE TABLE IF NOT EXISTS checkin_logs (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 account_id INTEGER NOT NULL REFERENCES accounts(id),
                 ts TEXT NOT NULL,
                 credits_earned REAL DEFAULT 0,
                 new_total REAL DEFAULT 0,
                 streak INTEGER DEFAULT 0,
                 status TEXT NOT NULL,
                 error TEXT DEFAULT ''
             );
         """)
         row = conn.execute("SELECT id FROM admins WHERE username=?", ("XIaoL",)).fetchone()
         if not row:
             pw_hash = hashlib.sha256("breezeblue-admin".encode()).hexdigest()
             conn.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
                          ("XIaoL", pw_hash, now_iso()))


@asynccontextmanager
async def lifespan(app: FastAPI):
     init_db()
     yield


app = FastAPI(title="BreezeBlue Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class LoginIn(BaseModel):
     username: str
     password: str


@app.post("/api/login")
async def login(body: LoginIn):
     pw_hash = hashlib.sha256(body.password.encode()).hexdigest()
     with db() as conn:
         row = conn.execute("SELECT * FROM admins WHERE username=? AND password_hash=?",
                            (body.username, pw_hash)).fetchone()
         if row:
             token = secrets.token_urlsafe(32)
             conn.execute("INSERT INTO sessions (token, username, expires_at) VALUES (?, ?, ?)",
                          (token, body.username, time.time() + SESSION_TTL))
             return {"token": token, "username": body.username}
     raise HTTPException(401, "用户名或密码错误")


def get_admin(request: Request) -> str:
     auth = request.headers.get("Authorization", "")
     if not auth.startswith("Bearer "):
         raise HTTPException(401, "未登录")
     token = auth[7:]
     with db() as conn:
         row = conn.execute("SELECT * FROM sessions WHERE token=? AND expires_at>=?",
                            (token, time.time())).fetchone()
         if row:
             return row["username"]
     raise HTTPException(401, "登录已过期")


class AccountIn(BaseModel):
     email: str
     password: str
     api_key: str = ""
     note: str = ""


@app.post("/api/accounts")
async def add_account(body: AccountIn, admin: str = Depends(get_admin)):
     with db() as conn:
         try:
             conn.execute("INSERT INTO accounts (email, password, api_key, note, created_at) VALUES (?, ?, ?, ?, ?)",
                          (body.email, body.password, body.api_key, body.note, now_iso()))
         except sqlite3.IntegrityError:
             raise HTTPException(400, "该邮箱已存在")


@app.get("/api/accounts")
async def list_accounts(admin: str = Depends(get_admin)):
     with db() as conn:
         rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
         return [dict(r) for r in rows]


@app.delete("/api/accounts/{id}")
async def delete_account(id: int, admin: str = Depends(get_admin)):
     with db() as conn:
         conn.execute("DELETE FROM accounts WHERE id=?", (id,))


@app.post("/api/accounts/{id}/checkin")
async def checkin_account(id: int, admin: str = Depends(get_admin)):
     with db() as conn:
         acc = conn.execute("SELECT * FROM accounts WHERE id=?", (id,)).fetchone()
         if not acc:
             raise HTTPException(404, "账户不存在")

     log_entry = {
         "account_id": id,
         "ts": now_iso(),
         "status": "success",
         "credits_earned": 0,
         "new_total": acc["credits"],
         "streak": acc["checkin_streak"] + 1
     }

     with db() as conn:
         conn.execute("""INSERT INTO checkin_logs 
                         (account_id, ts, credits_earned, new_total, streak, status, error)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (log_entry["account_id"], log_entry["ts"],
                       log_entry["credits_earned"], log_entry["new_total"],
                       log_entry["streak"], log_entry["status"], ""))
         conn.execute("UPDATE accounts SET checkin_streak=?, last_checkin=? WHERE id=?",
                      (log_entry["streak"], log_entry["ts"], id))

     return log_entry


@app.post("/api/checkin-all")
async def checkin_all(admin: str = Depends(get_admin)):
     with db() as conn:
         rows = conn.execute("SELECT id FROM accounts WHERE enabled=1").fetchall()

     results = []
     for row in rows:
         try:
             result = await checkin_account(row["id"], admin)
             results.append({"id": row["id"], "status": "success", "result": result})
         except Exception as e:
             results.append({"id": row["id"], "status": "error", "error": str(e)})

     return {"total": len(rows), "results": results}


@app.get("/api/logs")
async def get_logs(limit: int = 100, admin: str = Depends(get_admin)):
     with db() as conn:
         rows = conn.execute("""
             SELECT l.*, a.email 
             FROM checkin_logs l 
             JOIN accounts a ON l.account_id=a.id 
             ORDER BY l.id DESC LIMIT ?
         """, (limit,)).fetchall()
         return [dict(r) for r in rows]


@app.get("/api/stats")
async def get_stats(admin: str = Depends(get_admin)):
     try:
         with db() as conn:
             total_accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
             total_credits = conn.execute("SELECT COALESCE(SUM(credits),0) FROM accounts").fetchone()[0]
             today = datetime.now().strftime("%Y-%m-%d")
             today_checkins = conn.execute("""
                 SELECT COUNT(*) FROM checkin_logs l 
                 WHERE l.ts LIKE ? AND l.status=?
             """, (today + "%", "success")).fetchone()[0]

         return {
             "total_accounts": total_accounts,
             "total_credits": total_credits,
             "today_checkins": today_checkins
         }
     except Exception as e:
         import traceback
         tb = traceback.format_exc()
         return JSONResponse({"error": str(e), "traceback": tb}, status_code=500)


@app.get("/")
async def index():
     return FileResponse(str(STATIC_DIR / "index.html"))
