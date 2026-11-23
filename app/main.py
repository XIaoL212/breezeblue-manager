import os
import sqlite3
import hashlib
import json
import secrets
import time
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'breezeblue.db')

SESSION_TTL = 86400

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def now_iso():
    return datetime.now(timezone.utc).isoformat()

import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import breezeblue_client as bb

async def breezeblue_web_login(email, password, fingerprint=None):
    if not fingerprint:
        fingerprint = bb.generate_fingerprint()
    try:
        async with bb.httpx.AsyncClient(timeout=75, follow_redirects=True) as client:
            response = await client.post(
                bb.BREEZEBLUE_BASE_URL + '/api/auth/email/continue',
                headers=bb.breezeblue_login_headers(fingerprint),
                json={'email': email.strip(), 'password': password, 'locale': 'zh', 'next_path': '/app/home'},
            )
    except Exception as exc:
        raise HTTPException(502, f'无法连接 BreezeBlue：{exc}')
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        message = str(payload.get('detail') or payload.get('error') or response.text)[:500]
        raise HTTPException(401, f'BreezeBlue 登录失败：{message}')
    if str(payload.get('status') or '') != 'authenticated':
        raise HTTPException(401, 'BreezeBlue 登录未完成')
    session_cookie = response.cookies.get('breeze_blue_session') or ''
    if not session_cookie:
        for header in response.headers.get_list('set-cookie'):
            match = bb.re.search(r'(?:^|;\\s*)breeze_blue_session=([^;]+)', header)
            if match:
                session_cookie = match.group(1)
                break
    if not session_cookie:
        raise HTTPException(502, 'BreezeBlue 未返回会话 Cookie')
    return {'cookie': 'breeze_blue_session=' + session_cookie, 'user': payload.get('user') or {}}

async def breezeblue_web_request(row, method, path, json_body=None):
    cookie = row['web_session'] or ''
    if not cookie:
        raise HTTPException(409, 'BreezeBlue 会话尚未登录')
    try:
        fingerprint = json.loads(row['fingerprint_json'] or '{}')
    except Exception:
        fingerprint = bb.generate_fingerprint()
    headers = bb.breezeblue_login_headers(fingerprint)
    headers['Cookie'] = cookie
    headers['Referer'] = bb.BREEZEBLUE_BASE_URL + '/app/developer/api-keys'
    async with bb.httpx.AsyncClient(timeout=75, follow_redirects=True) as client:
        return await client.request(method.upper(), bb.BREEZEBLUE_BASE_URL + path, headers=headers, json=json_body)

async def breezeblue_auto_login(account_id):
    with db() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
    if not row:
        raise HTTPException(404, 'BreezeBlue 账户不存在')
    result = await breezeblue_web_login(row['email'], row['password'])
    now = now_iso()
    with db() as conn:
        try:
            fingerprint = json.loads(row['fingerprint_json'] or '{}')
        except Exception:
            fingerprint = bb.generate_fingerprint()
        conn.execute('UPDATE accounts SET web_session=?, fingerprint_json=?, last_error=?, updated_at=? WHERE id=?',
                     (result['cookie'], json.dumps(fingerprint), '', now, int(account_id)))
    return result

async def breezeblue_claim_daily(account_id):
    with db() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
    if not row:
        raise HTTPException(404, 'BreezeBlue 账户不存在')
    if not row['web_session'] and row['email']:
        await breezeblue_auto_login(account_id)
        with db() as conn:
            row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
    try:
        response = await breezeblue_web_request(row, 'POST', '/api/credits/daily-claim')
    except HTTPException as e:
        if '尚未登录' in str(e.detail):
            await breezeblue_auto_login(account_id)
            with db() as conn:
                row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
            response = await breezeblue_web_request(row, 'POST', '/api/credits/daily-claim')
        else:
            raise
    try:
        payload = response.json()
    except Exception:
        payload = {'detail': response.text[:500]}
    if response.status_code >= 400:
        if response.status_code in (401, 403):
            await breezeblue_auto_login(account_id)
            with db() as conn:
                row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
            response = await breezeblue_web_request(row, 'POST', '/api/credits/daily-claim')
            try:
                payload = response.json()
            except Exception:
                payload = {'detail': response.text[:500]}
        text = str(payload.get('detail') or payload.get('error') or payload)
        raise HTTPException(502, f'BreezeBlue 签到失败：{text[:400]}')
    summary_response = await breezeblue_web_request(row, 'GET', '/api/credits/summary')
    try:
        summary = summary_response.json()
    except Exception:
        summary = {}
    claimed_today = bool(summary.get('daily_claim_claimed_today'))
    balance = float(summary.get('balance') or 0)
    now = now_iso()
    with db() as conn:
        conn.execute('UPDATE accounts SET last_checkin=?, last_checkin_at=?, credits=?, total_credits=?, updated_at=? WHERE id=?',
                     (now, now, balance, balance, now, int(account_id)))
    return {'ok': True, 'already_claimed': claimed_today, 'balance': balance}

async def breezeblue_sync_account(account_id):
    with db() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
    if not row:
        raise HTTPException(404, 'BreezeBlue 账户不存在')
    if not row['web_session']:
        await breezeblue_auto_login(account_id)
        with db() as conn:
            row = conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()
    response = await breezeblue_web_request(row, 'GET', '/api/credits/summary')
    try:
        summary = response.json()
    except Exception:
        summary = {}
    balance = float(summary.get('balance') or 0)
    now = now_iso()
    with db() as conn:
        conn.execute('UPDATE accounts SET credits=?, total_credits=?, last_sync=?, updated_at=? WHERE id=?',
                     (balance, balance, now, now, int(account_id)))
    return {'ok': True, 'balance': balance}


def init_db():
    with db() as conn:
        conn.executescript('''
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
                web_session TEXT DEFAULT '',
                fingerprint_json TEXT DEFAULT '{}',
                api_key TEXT DEFAULT '',
                credits REAL DEFAULT 0,
                total_credits REAL DEFAULT 0,
                last_checkin TEXT,
                last_checkin_at TEXT DEFAULT '',
                last_sync TEXT,
                checkin_streak INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                last_error TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
        ''')
        pw_hash = hashlib.sha256('55588800qq'.encode()).hexdigest()
        row = conn.execute('SELECT id FROM admins WHERE username=?', ('XIaoL',)).fetchone()
        if row:
            conn.execute('UPDATE admins SET password_hash=? WHERE username=?', (pw_hash, 'XIaoL'))
        else:
            conn.execute('INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)',
                         ('XIaoL', pw_hash, now_iso()))

class AdminIn(BaseModel):
    username: str
    password: str

class AccountIn(BaseModel):
    email: str
    password: str
    api_key: Optional[str] = ''
    note: Optional[str] = ''

class LoginIn(BaseModel):
    username: str
    password: str

app = FastAPI(title='BreezeBlue Manager')

@app.on_event('startup')
async def startup():
    init_db()

@app.post('/api/admin')
async def create_admin(body: AdminIn):
    pw_hash = hashlib.sha256(body.password.encode()).hexdigest()
    with db() as conn:
        conn.execute('INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)',
                     (body.username, pw_hash, now_iso()))
    return {'ok': True}

@app.post('/api/login')
async def login(body: LoginIn):
    pw_hash = hashlib.sha256(body.password.encode()).hexdigest()
    with db() as conn:
        row = conn.execute('SELECT * FROM admins WHERE username=? AND password_hash=?',
                           (body.username, pw_hash)).fetchone()
        if row:
            token = secrets.token_urlsafe(32)
            conn.execute('INSERT INTO sessions (token, username, expires_at) VALUES (?, ?, ?)',
                         (token, body.username, time.time() + SESSION_TTL))
            return {'token': token, 'username': body.username}
    raise HTTPException(401, '用户名或密码错误')

def get_admin(request: Request) -> str:
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        raise HTTPException(401, '未登录')
    with db() as conn:
        row = conn.execute('SELECT * FROM sessions WHERE token=? AND expires_at>?',
                           (token, time.time())).fetchone()
        if not row:
            raise HTTPException(401, '会话过期')
        return row['username']

@app.post('/api/accounts')
async def add_account(body: AccountIn, admin: str = Depends(get_admin)):
    try:
        with db() as conn:
            conn.execute('INSERT INTO accounts (email, password, api_key, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
                         (body.email, body.password, body.api_key, body.note, now_iso(), now_iso()))
            row = conn.execute('SELECT * FROM accounts WHERE email=?', (body.email,)).fetchone()
            return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(400, '该邮箱已存在')

@app.get('/api/accounts')
async def list_accounts(admin: str = Depends(get_admin)):
    with db() as conn:
        rows = conn.execute('SELECT * FROM accounts ORDER BY id').fetchall()
        return [dict(r) for r in rows]

@app.delete('/api/accounts/{aid}')
async def delete_account(aid: int, admin: str = Depends(get_admin)):
    with db() as conn:
        conn.execute('DELETE FROM accounts WHERE id=?', (aid,))
    return {'ok': True}

@app.post('/api/accounts/{aid}/checkin')
async def checkin_account(aid: int, admin: str = Depends(get_admin)):
    try:
        result = await breezeblue_claim_daily(aid)
        with db() as conn:
            conn.execute('INSERT INTO checkin_logs (account_id, ts, status) VALUES (?, ?, ?)',
                         (aid, now_iso(), 'success'))
        return result
    except Exception as e:
        with db() as conn:
            conn.execute('INSERT INTO checkin_logs (account_id, ts, status, error) VALUES (?, ?, ?, ?)',
                         (aid, now_iso(), 'failed', str(e)[:500]))
            conn.execute('UPDATE accounts SET last_error=? WHERE id=?', (str(e)[:500], aid))
        raise HTTPException(500, f'签到失败: {e}')

@app.post('/api/accounts/{aid}/login')
async def login_account(aid: int, admin: str = Depends(get_admin)):
    await breezeblue_auto_login(aid)
    with db() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
    return {'ok': True, 'item': dict(row)}

@app.post('/api/accounts/{aid}/sync')
async def sync_account(aid: int, admin: str = Depends(get_admin)):
    return await breezeblue_sync_account(aid)

@app.get('/api/logs')
async def get_logs(admin: str = Depends(get_admin)):
    with db() as conn:
        rows = conn.execute('SELECT * FROM checkin_logs ORDER BY id DESC LIMIT 50').fetchall()
        return [dict(r) for r in rows]

@app.post('/api/checkin/all')
async def checkin_all(admin: str = Depends(get_admin)):
    results = {'success': 0, 'failed': 0, 'items': []}
    with db() as conn:
        accounts = conn.execute('SELECT id, email FROM accounts').fetchall()
    for account in accounts:
        try:
            await checkin_account(account['id'], admin)
            results['success'] += 1
            results['items'].append({'account_id': account['id'], 'email': account['email'], 'ok': True})
        except Exception as e:
            results['failed'] += 1
            results['items'].append({'account_id': account['id'], 'email': account['email'], 'ok': False, 'error': str(e)[:200]})
    return results

@app.get('/api/stats')
async def get_stats(admin: str = Depends(get_admin)):
    with db() as conn:
        account_count = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        total_credits = conn.execute('SELECT COALESCE(SUM(credits), 0) FROM accounts').fetchone()[0]
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_checkins = conn.execute('SELECT COUNT(*) FROM checkin_logs WHERE ts >= ?', (today,)).fetchone()[0]
    return {'account_count': account_count, 'total_credits': total_credits, 'today_checkins': today_checkins}

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

@app.get('/')
async def index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))

