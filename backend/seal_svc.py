"""封批升代 + 快照清单。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import SealIn
from validators import require_calibration_fresh, validate_batch_sealable

router = APIRouter(tags=["seal"])


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, b.batch_code FROM seal_snapshots s
               JOIN temp_batches b ON b.id=s.batch_id ORDER BY s.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/seals")
def seal_batch(body: SealIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"batch:{body.batch_id}"
    with connect() as conn:
        try:
            require_lock(conn, lock_key, user)
            batch = conn.execute(
                """SELECT b.*, p.calibration_due, p.probe_code
                   FROM temp_batches b JOIN probes p ON p.id=b.probe_id
                   WHERE b.id=?""",
                (body.batch_id,),
            ).fetchone()
            if not batch:
                raise HTTPException(404, "批次不存在")
            open_exc = conn.execute(
                """SELECT COUNT(*) c FROM excursion_cursors
                   WHERE batch_id=? AND status='开放'""",
                (body.batch_id,),
            ).fetchone()["c"]
            bdict = dict(batch)
            bdict["temp_points"] = batch["temp_points"]
            validate_batch_sealable(bdict, open_exc)
            require_calibration_fresh(batch)
            gen = int(batch["generation"])
            manifest = body.manifest or json.dumps(
                {
                    "batch_code": batch["batch_code"],
                    "generation": gen + 1,
                    "point_count": len(json.loads(batch["temp_points"] or "[]")),
                    "min": batch["min_observed"],
                    "max": batch["max_observed"],
                },
                ensure_ascii=False,
            )
            now = now_iso()
            new_generation = gen + 1
            # 甲路径（docs/seal_generation_minutes.md 裁决）：
            # 先插 seal_snapshots 当代正式行，再抬代计数，同一事务内零增一行，
            # 清单行写不进则整笔回滚，代次绝不先移。禁止影子草案/事后晋升（乙路封死）。
            cur = conn.execute(
                """INSERT INTO seal_snapshots(batch_id,batch_generation,manifest,
                   status,sealed_at) VALUES (?,?,?,?,?)""",
                (body.batch_id, new_generation, manifest, "正式", now),
            )
            seal_id = cur.lastrowid
            updated = conn.execute(
                """UPDATE temp_batches SET status='已封批', generation=?,
                   sealed_at=? WHERE id=? AND status!='已封批'""",
                (new_generation, now, body.batch_id),
            )
            if updated.rowcount != 1:
                raise HTTPException(409, "批次状态不允许封批")
            write_audit(
                conn,
                user,
                "batch_seal",
                batch["batch_code"],
                {
                    "seal_id": seal_id,
                    "generation": new_generation,
                    "manifest_ok": True,
                },
            )
            conn.commit()
            return {
                "seal_id": seal_id,
                "batch_code": batch["batch_code"],
                "generation": new_generation,
                "status": "已封批",
                "manifest_complete": True,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            # 回滚业务后再释放锁并单独提交，避免锁删除被回滚导致死锁
            release(conn, lock_key, user)
            conn.commit()