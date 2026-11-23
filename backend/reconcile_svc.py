"""仓间配额 vs 在批差额对账 + 互证只读。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["reconcile"])


def _chamber_recon(conn, chamber) -> dict:
    cid = chamber["id"]
    quota = int(chamber["quota_batches"])
    active = conn.execute(
        """SELECT COUNT(*) c FROM temp_batches
           WHERE chamber_id=? AND status NOT IN ('已封批','作废')""",
        (cid,),
    ).fetchone()["c"]
    sealed = conn.execute(
        """SELECT COUNT(*) c FROM temp_batches
           WHERE chamber_id=? AND status='已封批'""",
        (cid,),
    ).fetchone()["c"]
    # 甲路径口径：只认 status='正式' 的快照清单行；草案/影子不计，封批代无清单即空档
    seal_gap = conn.execute(
        """SELECT COUNT(*) c FROM temp_batches b
           WHERE b.chamber_id=? AND b.status='已封批'
             AND NOT EXISTS(
               SELECT 1 FROM seal_snapshots s
               WHERE s.batch_id=b.id AND s.batch_generation=b.generation
                 AND s.status='正式')""",
        (cid,),
    ).fetchone()["c"]
    gap = quota - active
    ok = active <= quota
    return {
        "chamber_id": cid,
        "chamber_code": chamber["chamber_code"],
        "region": chamber["region"],
        "quota_batches": quota,
        "active_batches": active,
        "sealed_batches": sealed,
        "seal_gap_batches": seal_gap,
        "quota_left": gap,
        "balanced": ok,
        "proof": {
            "formula": "active_batches <= quota_batches",
            "left": active,
            "right": quota,
        },
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        chambers = conn.execute("SELECT * FROM cold_chambers ORDER BY id").fetchall()
        items = [_chamber_recon(conn, c) for c in chambers]
        bad = [i for i in items if not i["balanced"]]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "balanced": len(items) - len(bad),
                "unbalanced": len(bad),
            },
        }


@router.get("/api/reconcile/{chamber_code}")
def reconcile_one(chamber_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        ch = conn.execute(
            "SELECT * FROM cold_chambers WHERE chamber_code=?", (chamber_code,)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "仓间不存在")
        return _chamber_recon(conn, ch)


@router.get("/api/proof/batch/{batch_code}")
def batch_proof(batch_code: str, authorization: AuthHeader = None):
    """批次互证：封批代 + 温谱导出 + 开放游标。"""
    require_user(authorization)
    with connect() as conn:
        batch = conn.execute(
            "SELECT * FROM temp_batches WHERE batch_code=?", (batch_code,)
        ).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        open_exc = conn.execute(
            """SELECT COUNT(*) c FROM excursion_cursors
               WHERE batch_id=? AND status='开放'""",
            (batch["id"],),
        ).fetchone()["c"]
        exports = conn.execute(
            """SELECT COUNT(*) c FROM spectrum_exports
               WHERE batch_id=? AND status='已导出'""",
            (batch["id"],),
        ).fetchone()["c"]
        sealed = batch["status"] == "已封批"
        spectrum_ok = (exports >= 1) if sealed else True
        # 甲路径：快照清单互证按正式行实测，封批代无正式清单行即空档显红，
        # 影子/草案行不计，禁止恒绿
        snapshot_count = conn.execute(
            """SELECT COUNT(*) c FROM seal_snapshots
               WHERE batch_id=? AND batch_generation=? AND status='正式'""",
            (batch["id"], batch["generation"]),
        ).fetchone()["c"]
        manifest_ok = (snapshot_count >= 1) if sealed else True
        return {
            "batch_code": batch_code,
            "status": batch["status"],
            "generation": batch["generation"],
            "open_excursions": open_exc,
            "spectrum_exports": exports,
            "sealed": sealed,
            "excursion_closed": open_exc == 0,
            "spectrum_matches_seal": spectrum_ok,
            "seal_manifest_ok": manifest_ok,
            "seal_snapshot_count": snapshot_count,
            "proof": {
                "formula": "sealed => spectrum>=1 and open_excursions==0",
                "sealed": sealed,
                "open_excursions": open_exc,
                "exports": exports,
            },
        }