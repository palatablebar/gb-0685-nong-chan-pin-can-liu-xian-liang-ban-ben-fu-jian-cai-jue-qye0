import csv,hashlib,json
from pathlib import Path
from decimal import Decimal
from app.errors import DomainError
from app.config import NATIONAL_STANDARD

DATA_DIR=Path(__file__).resolve().parents[3]/'sample_data'
# 裁决“出证当日”的参照日：用于反事实重算（新附则已生效）。
CERTIFY_REFERENCE_DAY='2026-09-15'

def annexes():
    with (DATA_DIR/'annexes.csv').open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

def annex_text(annex):
    return (DATA_DIR/annex['text_file']).read_bytes()

def annex_for_day(sampled, registry=None):
    """附则按采样日落入的生效区间选择，半开区间 [effective_at, superseded_at)。"""
    registry=registry if registry is not None else annexes()
    hits=[a for a in registry if a['effective_at']<=sampled and (not a['superseded_at'] or sampled<a['superseded_at'])]
    if len(hits)!=1:raise DomainError('ANNEX','采样日的地方附则缺失或重叠')
    return hits[0]

def digest(payload):
    if isinstance(payload,str):payload=payload.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def _limit_for(rows, product, analyte, sampled):
    hits=[r for r in rows if r['product']==product and r['analyte']==analyte and r['start']<=sampled<r['end']]
    return hits[0] if len(hits)==1 else None

def _source_measurement(state, calc):
    for r in state.get('measurements',[]):
        if r['id']==calc['id']:return r
    raise DomainError('TRACE','计算证据缺少来源报告')

def _counterfactual_code(calc, current_limit):
    """假设整体套用出证当日限量，按已冻结的检出区间重算结论。"""
    threshold=Decimal(current_limit);lower=Decimal(calc['lower']);upper=Decimal(calc['upper'])
    if upper<=threshold:return 'PASS'
    if lower>threshold:return 'FAIL'
    return 'UNCERTAIN'

def adjudicate(state):
    """对 m6 之后的状态做依据裁决，产出逐项目组合依据矩阵与快照文档（不落库）。"""
    registry=annexes()
    with (DATA_DIR/'limits.csv').open(encoding='utf-8-sig',newline='') as f:
        limits=list(csv.DictReader(f))
    matrix=[]
    for calc in state['calculations']:
        source=_source_measurement(state,calc)
        annex=annex_for_day(source['sampled'],registry)
        today_row=_limit_for(limits,source['product'],calc['analyte'],CERTIFY_REFERENCE_DAY)
        current_limit=str(Decimal(today_row['limit_mgkg'])) if today_row else None
        shifted=bool(current_limit and Decimal(current_limit)!=Decimal(calc['limit']))
        cf_code=_counterfactual_code(calc,current_limit) if shifted else calc['code']
        if shifted and cf_code!=calc['code']:
            reason='RETEST_SHIELDED'
        elif annex['superseded_at']:
            reason='OLD_ANNEX_LOCKED'
        else:
            reason='CURRENT_ANNEX'
        matrix.append({
            'sample_id':calc['sample_id'],'batch':calc['batch'],'product':source['product'],
            'analyte':calc['analyte'],'report_id':calc['id'],'replaces':calc.get('replaces') or '',
            'sampled':source['sampled'],'reported':source['reported'],
            'national_standard':NATIONAL_STANDARD,'annex_id':annex['annex_id'],
            'annex_title':annex['title'],'annex_effective_at':annex['effective_at'],
            'annex_superseded_at':annex['superseded_at'] or '',
            'limit_id':calc['standard'],'limit':calc['limit'],'code':calc['code'],
            'current_limit_today':current_limit or calc['limit'],'limit_changed':shifted,
            'counterfactual_code':cf_code,'reason':reason,
        })
    batches=sorted({m['batch'] for m in matrix})
    rule_lines=[
        '国家标准 GB 2763-2021 未换版，报告正文只写总标准编号。',
        '地方附则按采样日生效区间半开匹配；同一样本初检、复检同采样日，适用同一附则。',
        '采样日落在旧附则期内的项目，即使复检晚于新附则生效日，仍锁定旧附则，新附则不溯及既往。',
        '不得整体套用新附则：反事实重判会改写已确认项目的，逐条登记 RETEST_SHIELDED。',
        '冻结后项目结论、批次判级与归档只认本快照编号；同源重跑返回同一快照，不新增。',
    ]
    document={
        'national_standard':NATIONAL_STANDARD,
        'annex_ids':sorted({m['annex_id'] for m in matrix}),
        'annex_registry':registry,
        'annex_digests':{a['annex_id']:digest(annex_text(a)) for a in registry},
        'basis_matrix':matrix,'batches':batches,'rule_lines':rule_lines,
        'run_id':state.get('run_id','preview'),'manifest':state.get('manifest',{}),
        'reconcile_digest':digest(json.dumps(state.get('reconcile',{}),sort_keys=True,ensure_ascii=False)),
    }
    document['document_digest']=digest(_canonical(document))
    state['adjudication']={'matrix':matrix,'annex_ids':document['annex_ids'],'rule_lines':rule_lines,
        'document_digest':document['document_digest'],
        'counterfactual_changes':sum(1 for m in matrix if m['counterfactual_code']!=m['code'])}
    state['snapshot_document']=document
    return state

def _canonical(document):
    return json.dumps(document,sort_keys=True,ensure_ascii=False,separators=(',',':'))
