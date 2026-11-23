"""BUGFIX-02 封批升代 × 快照清单：甲/乙/争用三失败测。

口径见 docs/seal_generation_minutes.md（裁决甲路径，乙路封死）：
- 甲：代次正式上移与 seal_snapshots 正式行必须同事务零增一行，否则整笔回滚；
- 乙：不设影子/草案晋升通道，对账互证只认真式清单行，空档不得亮齐；
- 争用：两人同批抢升代，后到仅在 contention_log 留痕，不落代、不插清单。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db
from locks import release, try_acquire

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "seal_gap_seed.json"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _load_gap_fixture() -> dict:
    """读本机文件库夹具，造出「升代成功却无快照清单行」的空档批次。"""
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert rows, "夹具不能为空"
    with connect() as conn:
        for row in rows:
            # 夹具口径：snapshot_rows 必须为空档，绝不偷偷补清单
            assert row["snapshot_rows"] == []
            ch = conn.execute(
                "SELECT id FROM cold_chambers WHERE chamber_code=?",
                (row["chamber_code"],),
            ).fetchone()
            probe = conn.execute(
                "SELECT id FROM probes WHERE probe_code=?", (row["probe_code"],)
            ).fetchone()
            conn.execute(
                """INSERT INTO temp_batches(batch_code,chamber_id,probe_id,cargo_ref,
                   temp_points,generation,status,min_observed,max_observed,
                   created_at,sealed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["batch_code"],
                    ch["id"],
                    probe["id"],
                    row["cargo_ref"],
                    json.dumps(row["temp_points"], ensure_ascii=False),
                    int(row["generation"]),
                    row["status"],
                    row["min_observed"],
                    row["max_observed"],
                    "2026-11-22T19:00:00",
                    row["sealed_at"],
                ),
            )
        conn.commit()
    return rows[0]


def _formal_snapshot_count(batch_id: int, generation: int) -> int:
    with connect() as conn:
        return conn.execute(
            """SELECT COUNT(*) c FROM seal_snapshots
               WHERE batch_id=? AND batch_generation=? AND status='正式'""",
            (batch_id, generation),
        ).fetchone()["c"]


# ---------------------------------------------------------------- 甲路径

def test_jia_seal_generation_must_insert_formal_snapshot_same_tx():
    """甲：封批成功即代已升，seal_snapshots 必须同事务零增一行当代正式行。"""
    c = TestClient(app)
    h = _auth(c)
    batch = next(
        b
        for b in c.get("/api/batches", headers=h).json()["items"]
        if b["batch_code"] == "TB-CK-HD-01-001"
    )
    old_gen = batch["generation"]
    resp = c.post("/api/seals", headers=h, json={"batch_id": batch["id"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generation"] == old_gen + 1
    assert body["seal_id"] is not None  # 正式清单行 id，不得再回 None

    # 盯死「代次已升清单仍零行」：当代正式行必须恰好一行
    assert _formal_snapshot_count(batch["id"], old_gen + 1) == 1
    with connect() as conn:
        snap = conn.execute(
            "SELECT * FROM seal_snapshots WHERE batch_id=? ORDER BY id",
            (batch["id"],),
        ).fetchall()
    assert len(snap) == 1
    assert snap[0]["batch_generation"] == old_gen + 1
    assert snap[0]["status"] == "正式"
    assert snap[0]["manifest"]

    proof = c.get(
        f"/api/proof/batch/{batch['batch_code']}", headers=h
    ).json()
    assert proof["seal_manifest_ok"] is True
    assert proof["seal_snapshot_count"] == 1


def test_jia_snapshot_write_failure_rolls_back_generation(monkeypatch):
    """甲：清单行零增则整笔回滚，代次与状态一律留在封批前。"""
    import db as db_mod
    import seal_svc

    c = TestClient(app)
    h = _auth(c)
    batch = next(
        b
        for b in c.get("/api/batches", headers=h).json()["items"]
        if b["batch_code"] == "TB-CK-HD-01-001"
    )

    class BoomConn(sqlite3.Connection):
        def execute(self, sql, params=()):
            if "INSERT INTO seal_snapshots" in sql:
                raise RuntimeError("模拟快照清单写入失败")
            return super().execute(sql, params)

    def patched_connect():
        conn = sqlite3.connect(db_mod.DB_PATH, timeout=30, factory=BoomConn)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(seal_svc, "connect", patched_connect)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/seals", headers=h, json={"batch_id": batch["id"]})
    assert resp.status_code == 500

    with connect() as conn:
        row = conn.execute(
            "SELECT generation,status FROM temp_batches WHERE id=?",
            (batch["id"],),
        ).fetchone()
        snaps = conn.execute(
            "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=?",
            (batch["id"],),
        ).fetchone()["c"]
        locks_left = conn.execute(
            "SELECT COUNT(*) c FROM chamber_locks WHERE lock_key=?",
            (f"batch:{batch['id']}",),
        ).fetchone()["c"]
    assert row["generation"] == batch["generation"]  # 代次未动
    assert row["status"] == "已入库"                  # 状态未动
    assert snaps == 0
    assert locks_left == 0                            # 回滚不留锁


# ---------------------------------------------------------------- 乙路径（封死）

def test_yi_shadow_draft_never_counts_reconcile_must_flag_gap():
    """乙：影子摘要未晋升不得对账亮齐；本仓无草案晋升通道，只认真式行。"""
    gap = _load_gap_fixture()
    c = TestClient(app)
    h = _auth(c)

    proof = c.get(
        f"/api/proof/batch/{gap['batch_code']}", headers=h
    ).json()
    assert proof["status"] == "已封批"
    assert proof["generation"] == 2
    # 空档批次互证必须显红，不得恒绿
    assert proof["seal_manifest_ok"] is False
    assert proof["seal_snapshot_count"] == 0

    # 仓间对账须把封批空档计入，不得假装清单齐全
    recon = c.get("/api/reconcile/CK-HN-03", headers=h).json()
    assert recon["seal_gap_batches"] >= 1

    # 直接落一条影子草案行：乙路草案在本仓永远不算齐，也没有晋升入口
    with connect() as conn:
        conn.execute(
            """INSERT INTO seal_snapshots(batch_id,batch_generation,manifest,
               status,sealed_at) VALUES (?,?,?,?,?)""",
            (
                conn.execute(
                    "SELECT id FROM temp_batches WHERE batch_code=?",
                    (gap["batch_code"],),
                ).fetchone()["id"],
                2,
                '{"shadow": true}',
                "影子草案",
                "2026-11-22T20:05:00",
            ),
        )
        conn.commit()
    proof2 = c.get(
        f"/api/proof/batch/{gap['batch_code']}", headers=h
    ).json()
    assert proof2["seal_manifest_ok"] is False
    assert proof2["seal_snapshot_count"] == 0

    # 已封批批次不得借再封批把草案“晋升”为正式行
    gap_id = _batch_id_by_code(gap["batch_code"])
    reseal = c.post("/api/seals", headers=h, json={"batch_id": gap_id})
    assert reseal.status_code == 409
    assert _formal_snapshot_count(gap_id, 2) == 0


def _batch_id_by_code(code: str) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT id FROM temp_batches WHERE batch_code=?", (code,)
        ).fetchone()["id"]


# ---------------------------------------------------------------- 争用

def _sealable_batch(c: TestClient, h: dict):
    return next(
        b
        for b in c.get("/api/batches", headers=h).json()["items"]
        if b["batch_code"] == "TB-CK-HD-01-001"
    )


def test_zhengyong_lock_window_loser_only_logs():
    """争用 A（确定性串行化）：持锁封批期间，后到者撞锁仅留痕不落代，
    先到者随后完成升代，代与清单只落一次。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_ops = _auth(c, "ops")
    batch = _sealable_batch(c, h_admin)
    lock_key = f"batch:{batch['id']}"

    # 第一窗先抢到封批锁并提交，锁对其他连接确定可见
    holder_conn = connect()
    try:
        assert try_acquire(holder_conn, lock_key, "admin") is True
        holder_conn.commit()

        # 第二窗同批点升代：撞锁 → 409，仅争用留痕，不动代次/清单
        loser = c.post(
            "/api/seals", headers=h_ops, json={"batch_id": batch["id"]}
        )
        assert loser.status_code == 409
        assert "争用" in loser.json()["detail"]
        with connect() as ro:
            gen_after_loser = ro.execute(
                "SELECT generation FROM temp_batches WHERE id=?", (batch["id"],)
            ).fetchone()["generation"]
            snaps_after_loser = ro.execute(
                "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=?",
                (batch["id"],),
            ).fetchone()["c"]
            contention = ro.execute(
                "SELECT * FROM contention_log WHERE lock_key=? AND actor='ops'",
                (lock_key,),
            ).fetchall()
        assert gen_after_loser == batch["generation"]
        assert snaps_after_loser == 0
        assert len(contention) == 1 and contention[0]["action"] == "争用"

        # 持锁者随后完成升代（同持锁人可重入），同事务落一行正式清单
        winner = c.post(
            "/api/seals", headers=h_admin, json={"batch_id": batch["id"]}
        )
        assert winner.status_code == 200
        assert winner.json()["seal_id"] is not None
    finally:
        release(holder_conn, lock_key, "admin")
        holder_conn.commit()
        holder_conn.close()

    with connect() as conn:
        gen = conn.execute(
            "SELECT generation FROM temp_batches WHERE id=?", (batch["id"],)
        ).fetchone()["generation"]
        snaps = conn.execute(
            "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=?",
            (batch["id"],),
        ).fetchone()["c"]
        formal = conn.execute(
            "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=? AND status='正式'",
            (batch["id"],),
        ).fetchone()["c"]
        total_contention = conn.execute(
            "SELECT COUNT(*) c FROM contention_log WHERE lock_key=? AND actor='ops'",
            (lock_key,),
        ).fetchone()["c"]
        lock_left = conn.execute(
            "SELECT COUNT(*) c FROM chamber_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()["c"]
    assert gen == batch["generation"] + 1
    assert snaps == 1 and formal == 1
    assert total_contention == 1      # 留痕不重复、不丢失
    assert lock_left == 0


def test_zhengyong_two_windows_same_batch_only_one_generation():
    """争用 B：两窗真·同时双击同批升代，仅一成，代与清单都只落一次。"""
    c = TestClient(app)
    h = _auth(c)
    batch = _sealable_batch(c, h)
    start = threading.Barrier(2)
    results: dict[str, int] = {}

    def window(who: str):
        cli = TestClient(app)
        head = _auth(cli, who)
        start.wait()
        r = cli.post("/api/seals", headers=head, json={"batch_id": batch["id"]})
        results[who] = r.status_code

    t1 = threading.Thread(target=window, args=("admin",))
    t2 = threading.Thread(target=window, args=("ops",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # 恰有一个成功，另一个被挡（锁争用或已封批拦截，二者皆为合法拒绝）
    assert sorted(results.values()) == [200, 409]
    with connect() as conn:
        gen = conn.execute(
            "SELECT generation FROM temp_batches WHERE id=?", (batch["id"],)
        ).fetchone()["generation"]
        snaps = conn.execute(
            "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=?",
            (batch["id"],),
        ).fetchone()["c"]
        formal = conn.execute(
            "SELECT COUNT(*) c FROM seal_snapshots WHERE batch_id=? AND status='正式'",
            (batch["id"],),
        ).fetchone()["c"]
        lock_left = conn.execute(
            "SELECT COUNT(*) c FROM chamber_locks WHERE lock_key=?",
            (f"batch:{batch['id']}",),
        ).fetchone()["c"]
    assert gen == batch["generation"] + 1   # 绝不双成功升两代
    assert snaps == 1 and formal == 1
    assert lock_left == 0
