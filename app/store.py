import sqlite3,json,os
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 依据裁决：全部只追加，不提供 UPDATE/DELETE；封存失败整笔回滚，旧活动快照保持有效。
    db.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, digest TEXT NOT NULL, document TEXT NOT NULL, raw_report_b64 TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, frozen_at TEXT NOT NULL, superseded_at TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS snapshot_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL, run_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS annex_archive (annex_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, title TEXT NOT NULL, effective_at TEXT NOT NULL, superseded_at TEXT, sha256 TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(annex_id,snapshot_id))')
    db.execute('CREATE TABLE IF NOT EXISTS raw_reports (run_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, raw_b64 TEXT NOT NULL, stored_at TEXT NOT NULL)')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)

# ---------- M8 依据裁决快照 ----------

def _now():
    from datetime import datetime,timezone
    return datetime.now(timezone.utc).isoformat()

def _row_to_snapshot(r):
    doc=json.loads(r['document'])
    return {'snapshot_id':r['id'],'run_id':r['run_id'],'digest':r['digest'],'status':r['status'],
        'created_at':r['created_at'],'frozen_at':r['frozen_at'],'superseded_at':r['superseded_at'],
        'document':doc,'raw_report_b64':r['raw_report_b64']}

def get_snapshot(snapshot_id):
    with connection() as db:r=db.execute('SELECT * FROM snapshots WHERE id=?',(snapshot_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','快照不存在',404)
    return _row_to_snapshot(r)

def list_snapshots():
    with connection() as db:
        rows=db.execute("SELECT * FROM snapshots ORDER BY CASE WHEN status='ACTIVE' THEN 0 ELSE 1 END,rowid DESC").fetchall()
    return [{k:_row_to_snapshot(r)[k] for k in ('snapshot_id','run_id','digest','status','created_at','frozen_at','superseded_at')} for r in rows]

def find_by_run(run_id):
    with connection() as db:r=db.execute('SELECT * FROM snapshots WHERE run_id=? ORDER BY rowid LIMIT 1',(run_id,)).fetchone()
    return _row_to_snapshot(r) if r else None

def active_snapshot():
    with connection() as db:r=db.execute("SELECT * FROM snapshots WHERE status='ACTIVE' ORDER BY rowid DESC LIMIT 1").fetchone()
    return _row_to_snapshot(r) if r else None

def audit_log(limit=100):
    with connection() as db:
        rows=db.execute('SELECT * FROM snapshot_audit ORDER BY id DESC LIMIT ?',(limit,)).fetchall()
    return [dict(r) for r in rows]

def annex_records(snapshot_id=None):
    with connection() as db:
        if snapshot_id:
            rows=db.execute('SELECT annex_id,snapshot_id,title,effective_at,superseded_at,sha256 FROM annex_archive WHERE snapshot_id=? ORDER BY annex_id',(snapshot_id,)).fetchall()
        else:
            rows=db.execute('SELECT annex_id,snapshot_id,title,effective_at,superseded_at,sha256 FROM annex_archive ORDER BY annex_id,snapshot_id').fetchall()
    return [dict(r) for r in rows]

def freeze_snapshot(snapshot_id, run_id, document, raw_report_b64, raw_sha256, fail_point=None):
    """单事务封存。任何异常（含 fail_point 注入的中断）都整体回滚，旧 ACTIVE 快照不动。"""
    now=_now()
    db=connection()
    try:
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT id FROM snapshots WHERE run_id=?',(run_id,)).fetchone()
        if existing:
            db.rollback();db.close()
            return get_snapshot(existing['id'])
        # 1) 原始报告独立留痕（同源重跑不新增）
        db.execute('INSERT OR IGNORE INTO raw_reports(run_id,sha256,raw_b64,stored_at) VALUES (?,?,?,?)',
            (run_id,raw_sha256,raw_report_b64,now))
        # 2) 旧活动快照转为 SUPERSEDED，仅状态位变化；正文与审计永久保留
        db.execute("UPDATE snapshots SET status='SUPERSEDED',superseded_at=? WHERE status='ACTIVE'",(now,))
        # 3) 写入新快照（只追加）
        db.execute("INSERT INTO snapshots(id,run_id,digest,document,raw_report_b64,status,created_at,frozen_at) VALUES (?,?,?,?,?,'ACTIVE',?,?)",
            (snapshot_id,run_id,document['document_digest'],json.dumps(document,ensure_ascii=False),raw_report_b64,now,now))
        # 4) 两版附则全文归档（每个快照各一份，旧版不丢）
        from app.modules.m8_snapshot.rules import annex_text
        for annex in document['annex_registry']:
            body=annex_text(annex).decode('utf-8')
            db.execute('INSERT OR IGNORE INTO annex_archive(annex_id,snapshot_id,title,effective_at,superseded_at,sha256,body) VALUES (?,?,?,?,?,?,?)',
                (annex['annex_id'],snapshot_id,annex['title'],annex['effective_at'],annex['superseded_at'],document['annex_digests'][annex['annex_id']],body))
        if fail_point=='before_commit':
            raise RuntimeError('INJECTED_INTERRUPT')
        db.execute('INSERT INTO snapshot_audit(snapshot_id,run_id,action,detail,at) VALUES (?,?,?,?,?)',
            (snapshot_id,run_id,'FREEZE','冻结组合依据快照 '+snapshot_id,now))
        db.commit()
    except Exception:
        db.rollback();db.close();raise
    db.close()
    return get_snapshot(snapshot_id)

def verify_binding(run_id):
    """项目结论、批次、归档只认活动快照：返回活动快照并校验其与运行批次的绑定。"""
    snap=active_snapshot()
    if snap is None:raise DomainError('NO_SNAPSHOT','尚未冻结依据快照，不得出证',409)
    if snap['run_id']!=run_id:
        raise DomainError('SNAPSHOT_MISMATCH','该批次未绑定当前活动快照，拒绝出证',409)
    return snap
