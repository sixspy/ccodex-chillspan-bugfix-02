"""仓间锁与争用登记（公共并发面）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def _record_contention(lock_key: str, actor: str, holder: str) -> None:
    """争用留痕走独立连接提交：后到者不写业务，痕迹也不得随业务回滚丢失。"""
    now = now_iso()
    with connect() as log_conn:
        log_conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "争用", f"持有者={holder}", now),
        )
        log_conn.commit()


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    row = conn.execute(
        "SELECT holder FROM chamber_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO chamber_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, holder, now_iso()),
        )
        return True
    if row["holder"] == holder:
        return True
    _record_contention(lock_key, holder, row["holder"])
    return False


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM chamber_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chamber_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, body.lock_key, user)
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}