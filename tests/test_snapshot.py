import json
import pytest
from app.workflow import run,load
from app import store
from app.modules.m8_snapshot.rules import annexes,annex_for_day,annex_text
from app.modules.m8_snapshot.export_out import encode,printable_html
from app.errors import DomainError

@pytest.fixture
def transition():
    return run(load('transition.csv'),{},8)

@pytest.fixture
def frozen(transition,tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'m8.db'))
    store.save(transition['run_id'],transition)
    import hashlib,base64
    doc=transition['snapshot_document']
    sid='SNP-'+doc['document_digest'][:16]
    raw=base64.b64decode(transition['source_b64'])
    return store.freeze_snapshot(sid,transition['run_id'],doc,transition['source_b64'],hashlib.sha256(raw).hexdigest())

def test_national_standard_unchanged(transition):
    assert {m['national_standard'] for m in transition['adjudication']['matrix']}=={'GB 2763-2021'}

def test_annex_chosen_by_sampling_day_not_report_day():
    reg=annexes()
    assert annex_for_day('2026-08-31',reg)['annex_id']=='DBS/026-2025'
    assert annex_for_day('2026-09-01',reg)['annex_id']=='DBS/026-2026'
    matrix={(m['sample_id'],m['analyte']):m for m in transition_adjudication()['matrix']}
    # 复检 2026-09-05 晚到，但采样日 2026-08-28 仍锁定旧附则
    assert matrix[('S101','P03')]['annex_id']=='DBS/026-2025'
    assert matrix[('S101','P03')]['report_id']=='10002'

def transition_adjudication():
    return run(load('transition.csv'),{},8)['adjudication']

def test_whole_swap_would_rewrite_confirmed_items():
    adj=transition_adjudication()
    shielded={m['report_id']:m for m in adj['matrix'] if m['reason']=='RETEST_SHIELDED'}
    # S101 P03 复检 0.012 旧限 0.02 为 PASS；整体换新限 0.01 会变 FAIL
    assert shielded['10002']['code']=='PASS' and shielded['10002']['counterfactual_code']=='FAIL'
    # S101 P04 已确认项目 0.03 旧限 0.05 PASS；新限 0.02 变 FAIL
    assert shielded['10003']['counterfactual_code']=='FAIL'
    assert adj['counterfactual_changes']==3

def test_post_effective_samples_use_current_annex():
    matrix={(m['sample_id'],m['analyte']):m for m in transition_adjudication()['matrix']}
    assert matrix[('S102','P03')]['reason']=='CURRENT_ANNEX'
    assert matrix[('S102','P04')]['code']=='FAIL' and matrix[('S102','P04')]['limit']=='0.02'

def test_digest_is_deterministic():
    a=run(load('transition.csv'),{},8)['adjudication']['document_digest']
    b=run(load('transition.csv'),{},8)['adjudication']['document_digest']
    assert a==b and len(a)==64

def test_interrupt_rolls_back_and_keeps_old_active(transition,tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'roll.db'))
    store.save(transition['run_id'],transition)
    doc=transition['snapshot_document'];sid='SNP-'+doc['document_digest'][:16]
    with pytest.raises(RuntimeError):
        store.freeze_snapshot(sid,transition['run_id'],doc,transition['source_b64'],'x',fail_point='before_commit')
    assert store.list_snapshots()==[] and store.active_snapshot() is None
    assert store.annex_records()==[]
    # 中断后正常封存仍成功
    import hashlib,base64
    raw=base64.b64decode(transition['source_b64'])
    snap=store.freeze_snapshot(sid,transition['run_id'],doc,transition['source_b64'],hashlib.sha256(raw).hexdigest())
    assert snap['status']=='ACTIVE'

def test_same_source_rerun_creates_no_new_snapshot(frozen,transition):
    before=len(store.list_snapshots())
    again=store.freeze_snapshot(frozen['snapshot_id'],transition['run_id'],transition['snapshot_document'],transition['source_b64'],'x')
    assert again['snapshot_id']==frozen['snapshot_id'] and len(store.list_snapshots())==before

def test_both_annex_versions_and_raw_report_kept(frozen,transition):
    recs={r['annex_id']:r for r in store.annex_records(frozen['snapshot_id'])}
    assert set(recs)=={'DBS/026-2025','DBS/026-2026'}
    for annex in annexes():
        assert recs[annex['annex_id']]['sha256']==frozen['document']['annex_digests'][annex['annex_id']]
    with store.connection() as db:
        rr=db.execute('SELECT * FROM raw_reports WHERE run_id=?',(transition['run_id'],)).fetchone()
    assert rr and rr['raw_b64']==transition['source_b64']

def test_superseded_snapshot_is_immutable(frozen,transition,tmp_path,monkeypatch):
    clean=run(load('clean.csv'),{},8);store.save(clean['run_id'],clean)
    import hashlib,base64
    raw=base64.b64decode(clean['source_b64'])
    sid2='SNP-'+clean['snapshot_document']['document_digest'][:16]
    store.freeze_snapshot(sid2,clean['run_id'],clean['snapshot_document'],clean['source_b64'],hashlib.sha256(raw).hexdigest())
    old=store.get_snapshot(frozen['snapshot_id'])
    assert old['status']=='SUPERSEDED' and old['document']==transition['snapshot_document']
    assert store.active_snapshot()['snapshot_id']==sid2

def test_binding_rejects_unbound_batch(frozen):
    assert store.verify_binding(frozen['run_id'])['snapshot_id']==frozen['snapshot_id']
    with pytest.raises(DomainError) as e:store.verify_binding('other-run')
    assert e.value.status==409 and e.value.code=='SNAPSHOT_MISMATCH'

def test_export_bundle_contains_everything(frozen):
    pack=json.loads(encode(frozen))
    assert len(pack['export_digest'])==64
    assert set(pack['annex_full_text'])=={'DBS/026-2025','DBS/026-2026'}
    assert pack['raw_report_b64']==frozen['raw_report_b64']
    html=printable_html(frozen)
    assert frozen['snapshot_id'] in html and '组合依据裁决快照' in html
