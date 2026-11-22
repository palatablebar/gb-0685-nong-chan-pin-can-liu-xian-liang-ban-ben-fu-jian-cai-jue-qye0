import base64
from flask import Blueprint,jsonify,request,render_template,Response
from app.web import api
from app.errors import DomainError
from app import store
from app.modules.m8_snapshot.export_out import encode,printable_html

bp=Blueprint('m8_snapshot',__name__)

def _snapshot_id(document):
    return 'SNP-'+document['document_digest'][:16]

@bp.get('/m8_snapshot')
def screen():
    from app.web import page
    return page(8,'m8_snapshot.html',extra={
        'snapshots':store.list_snapshots(),'active':store.active_snapshot(),
        'audit':store.audit_log(50),'annex_records':store.annex_records()})

@bp.route('/api/m8_snapshot',methods=['GET','POST'])
def preview():
    return api(8)

@bp.post('/api/snapshots/freeze')
def freeze():
    data=request.get_json(silent=True) or {}
    run_id=data.get('run_id')
    if not isinstance(run_id,str) or not run_id:raise DomainError('PAYLOAD','缺少 run_id')
    state=store.get(run_id)
    if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平，不得冻结依据')
    from app.modules.m8_snapshot.rules import adjudicate
    document=adjudicate(state)['snapshot_document']
    raw_b64=state.get('source_b64','')
    import hashlib
    raw_sha=hashlib.sha256(base64.b64decode(raw_b64) if raw_b64 else b'').hexdigest()
    snap=store.freeze_snapshot(_snapshot_id(document),run_id,document,raw_b64,raw_sha,
        fail_point='before_commit' if data.get('simulate_interrupt') else None)
    return jsonify({'snapshot':_summary(snap),'idempotent':snap['run_id']==run_id and store.find_by_run(run_id)['snapshot_id']==snap['snapshot_id']})

@bp.get('/api/snapshots')
def listing():
    return jsonify({'snapshots':store.list_snapshots(),'active':_summary(store.active_snapshot()),
        'audit':store.audit_log(),'annexes':store.annex_records()})

@bp.get('/api/snapshots/<snapshot_id>')
def detail(snapshot_id):
    return jsonify(_summary(store.get_snapshot(snapshot_id)))

@bp.get('/api/snapshots/<snapshot_id>/export.json')
def export_json(snapshot_id):
    snap=store.get_snapshot(snapshot_id)
    return Response(encode(snap),mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition':'attachment; filename='+snapshot_id+'.json'})

@bp.get('/snapshots/<snapshot_id>/print')
def print_page(snapshot_id):
    snap=store.get_snapshot(snapshot_id)
    return Response(printable_html(snap),mimetype='text/html; charset=utf-8')

def _summary(snap):
    if snap is None:return None
    return {'snapshot_id':snap['snapshot_id'],'run_id':snap['run_id'],'digest':snap['digest'],
        'status':snap['status'],'created_at':snap['created_at'],'frozen_at':snap['frozen_at'],
        'superseded_at':snap['superseded_at'],
        'annex_ids':snap['document']['annex_ids'],
        'matrix_size':len(snap['document']['basis_matrix']),
        'batches':snap['document']['batches']}
