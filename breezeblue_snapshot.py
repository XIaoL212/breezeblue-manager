"""Snapshot the local SQLite database to object storage and restore it on boot.

The deployed container has no persistent disk, so the account list and check-in
history would vanish on every redeploy. Snapshots are tiny (tens of KB) and are
written with a lexicographically sortable name so the newest one is trivial to
pick. Config comes from the environment because the database being backed up
cannot bootstrap its own backup location.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import breezeblue_s3 as s3

ENABLED = os.environ.get("BREEZEBLUE_S3_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
ENDPOINT = os.environ.get("BREEZEBLUE_S3_ENDPOINT", "")
BUCKET = os.environ.get("BREEZEBLUE_S3_BUCKET", "")
REGION = os.environ.get("BREEZEBLUE_S3_REGION", "eu-central-003")
PREFIX = os.environ.get("BREEZEBLUE_S3_PREFIX", "breezeblue-manager")
ACCESS_KEY = os.environ.get("BREEZEBLUE_S3_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("BREEZEBLUE_S3_SECRET_KEY", "")
SNAPSHOT_PREFIX = os.environ.get("BREEZEBLUE_SNAPSHOT_PREFIX", "snapshots")
RETENTION = int(os.environ.get("BREEZEBLUE_SNAPSHOT_RETENTION", "24"))


def enabled() -> bool:
    return bool(ENABLED and ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY)


def config() -> dict:
    return {
        "endpoint": ENDPOINT,
        "region": REGION,
        "bucket": BUCKET,
        "prefix": PREFIX,
        "access_key": ACCESS_KEY,
        "secret_key": SECRET_KEY,
        "timeout": float(os.environ.get("BREEZEBLUE_SNAPSHOT_TIMEOUT", "120")),
    }


def public_config() -> dict:
    """Config for the admin UI, with the secret reduced to a boolean."""
    return s3.s3_config_public(config()) | {
        "enabled": enabled(),
        "snapshot_prefix": SNAPSHOT_PREFIX,
        "retention": RETENTION,
        "interval_seconds": INTERVAL,
    }


def _newest_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ") + ".db"


async def restore(db_path: str) -> dict:
    """Download the newest snapshot when the local database has nothing to keep."""
    if not enabled():
        return {"restored": False, "reason": "s3 disabled"}
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and _account_count(db_path) > 0:
        return {"restored": False, "reason": "local database already has accounts"}
    try:
        listed = await s3.s3_list_objects(config(), SNAPSHOT_PREFIX)
    except Exception as exc:
        return {"restored": False, "reason": f"list failed: {exc}"}
    if not listed:
        return {"restored": False, "reason": "no snapshots found"}
    newest = sorted(listed, key=lambda item: item["key"])[-1]
    try:
        data = await s3.s3_get_object(config(), newest["key"])
    except Exception as exc:
        return {"restored": False, "reason": f"download failed: {exc}"}
    try:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(db_path + suffix)
            except OSError:
                pass
        handle = tempfile.NamedTemporaryFile(dir=os.path.dirname(db_path) or ".", delete=False, suffix=".db")
        handle.write(data)
        handle.close()
        os.replace(handle.name, db_path)
    except Exception as exc:
        return {"restored": False, "reason": f"write failed: {exc}"}
    return {"restored": True, "key": newest["key"], "bytes": len(data), "accounts": _account_count(db_path)}


def _account_count(db_path: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone()
            if not row or not row[0]:
                return 0
            return int(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


async def snapshot(db_path: str) -> dict:
    """Copy the live database (WAL folded in) and upload it, then prune old copies."""
    if not enabled():
        return {"ok": False, "reason": "s3 disabled"}
    key = f"{SNAPSHOT_PREFIX}/{_newest_key()}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        source = sqlite3.connect(db_path)
        try:
            dest = sqlite3.connect(tmp.name)
            try:
                with dest:
                    source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        with open(tmp.name, "rb") as handle:
            data = handle.read()
        url = await s3.s3_put_object(config(), key, data)
        pruned = await _prune()
        return {"ok": True, "key": key, "bytes": len(data), "pruned": pruned, "url": url}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:300], "key": key}
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


async def _prune() -> int:
    listed = await s3.s3_list_objects(config(), SNAPSHOT_PREFIX)
    stale = sorted(listed, key=lambda item: item["key"])[:-RETENTION] if RETENTION > 0 else []
    for item in stale:
        try:
            await s3.s3_delete_object(config(), item["key"])
        except Exception:
            pass
    return len(stale)


INTERVAL = float(os.environ.get("BREEZEBLUE_SNAPSHOT_INTERVAL", "10800"))


async def snapshot_loop(db_path: str) -> None:
    """Capture the boot state early, then keep snapshotting on the interval.

    A seeded account and its first check-in land within the first minute, so
    waiting out the full interval before the first snapshot would leave a window
    where a redeploy loses everything the container just learned.
    """
    await asyncio.sleep(60)
    while True:
        try:
            result = await snapshot(db_path)
            if result.get("ok"):
                print(f"breezeblue-snapshot: {result.get('key')} ({result.get('bytes')} bytes)", flush=True)
            else:
                print(f"breezeblue-snapshot-failed: {result.get('reason')}", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"breezeblue-snapshot-failed: {exc!r}", flush=True)
        await asyncio.sleep(INTERVAL)
