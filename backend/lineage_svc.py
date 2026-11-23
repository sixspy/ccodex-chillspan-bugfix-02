"""冷链血缘边登记。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import LineageIn

router = APIRouter(tags=["lineage"])


def _batch_seal_view(conn, batch_id: int | None) -> dict | None:
    if not batch_id:
        return None
    b = conn.execute(
        "SELECT id,batch_code,generation,status FROM temp_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if not b:
        return None
    snaps = conn.execute(
        """SELECT COUNT(*) c FROM seal_snapshots
           WHERE batch_id=? AND batch_generation=? AND status='正式'""",
        (batch_id, b["generation"]),
    ).fetchone()["c"]
    view = dict(b)
    view["manifest_complete"] = (snaps >= 1) if b["status"] == "已封批" else True
    view["seal_snapshot_count"] = snaps
    return view


@router.get("/api/lineage")
def list_edges(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.*, a.batch_code from_code, b.batch_code to_code
               FROM lineage_edges e
               JOIN temp_batches a ON a.id=e.from_batch_id
               LEFT JOIN temp_batches b ON b.id=e.to_batch_id
               ORDER BY e.id DESC"""
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["from_batch"] = _batch_seal_view(conn, r["from_batch_id"])
            d["to_batch"] = _batch_seal_view(conn, r["to_batch_id"])
            items.append(d)
        return {"items": items}


@router.post("/api/lineage")
def create_edge(body: LineageIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        src = conn.execute(
            "SELECT id FROM temp_batches WHERE id=?", (body.from_batch_id,)
        ).fetchone()
        if not src:
            raise HTTPException(404, "来源批次不存在")
        if body.to_batch_id:
            dst = conn.execute(
                "SELECT id FROM temp_batches WHERE id=?", (body.to_batch_id,)
            ).fetchone()
            if not dst:
                raise HTTPException(404, "目标批次不存在")
        cur = conn.execute(
            """INSERT INTO lineage_edges(from_batch_id,to_batch_id,edge_type,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (
                body.from_batch_id,
                body.to_batch_id,
                body.edge_type,
                body.detail,
                now_iso(),
            ),
        )
        write_audit(
            conn,
            user,
            "lineage_add",
            str(body.from_batch_id),
            {"edge_id": cur.lastrowid, "type": body.edge_type},
        )
        conn.commit()
        return {"id": cur.lastrowid, "edge_type": body.edge_type}


@router.get("/api/lineage/batch/{batch_id}")
def batch_lineage(batch_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        out_edges = conn.execute(
            "SELECT * FROM lineage_edges WHERE from_batch_id=?", (batch_id,)
        ).fetchall()
        in_edges = conn.execute(
            "SELECT * FROM lineage_edges WHERE to_batch_id=?", (batch_id,)
        ).fetchall()
        return {
            "outbound": [dict(e) for e in out_edges],
            "inbound": [dict(e) for e in in_edges],
            "batch": _batch_seal_view(conn, batch_id),
        }