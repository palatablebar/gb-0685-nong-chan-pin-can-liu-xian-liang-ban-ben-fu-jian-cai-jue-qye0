import json,hashlib
from app.modules.m8_snapshot.rules import annex_text

def bundle(snapshot):
    """可导出的标准快照证据包：裁决文档 + 两版附则全文 + 原始报告 + 摘要。"""
    document=snapshot['document']
    annex_bodies={a['annex_id']:annex_text(a).decode('utf-8') for a in document['annex_registry']}
    return {
        'snapshot_id':snapshot['snapshot_id'],'run_id':snapshot['run_id'],
        'status':snapshot['status'],'frozen_at':snapshot['frozen_at'],
        'document':document,'annex_full_text':annex_bodies,
        'raw_report_b64':snapshot.get('raw_report_b64',''),
        'export_digest':'',
    }

def encode(snapshot):
    pack=bundle(snapshot)
    pack['export_digest']=hashlib.sha256(
        json.dumps(pack,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    return json.dumps(pack,ensure_ascii=False,indent=2,sort_keys=True)

def printable_html(snapshot):
    document=snapshot['document']
    rows='\n'.join(
        '<tr><td>{sample_id}</td><td>{batch}</td><td>{analyte}</td><td>{report_id}</td>'
        '<td>{sampled}</td><td>{reported}</td><td>{annex_id}</td><td class="num">{limit}</td>'
        '<td>{code}</td><td>{counterfactual_code}</td><td>{reason}</td></tr>'.format(**m)
        for m in document['basis_matrix'])
    rules=''.join('<li>{}</li>'.format(x) for x in document['rule_lines'])
    annexes=''.join(
        '<p><b>{}</b>（{} 生效）sha256={}</p>'.format(a['annex_id'],a['effective_at'],document['annex_digests'][a['annex_id']])
        for a in document['annex_registry'])
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>标准快照 {sid}</title><style>
body{{font-family:'Noto Serif CJK SC',serif;margin:32px;color:#1a1a1a}}
h1{{font-size:20px}}table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border:1px solid #555;padding:5px 7px;text-align:left}}.num{{text-align:right}}
.meta{{font-size:12px;color:#444;margin:8px 0 18px}}.digest{{font-family:monospace;word-break:break-all}}
@media print{{.noprint{{display:none}}}}
</style></head><body>
<div class="eyebrow">STANDARD BASIS SNAPSHOT · 只认此件</div>
<h1>限量附则换版组合依据裁决快照</h1>
<div class="meta">快照编号 <b>{sid}</b> ｜ 运行批次 {rid} ｜ 状态 {status} ｜ 冻结时间 {frozen}<br>
国家标准总编号 <b>{gb}</b>（未换版）｜ 文档摘要 <span class="digest">{dgst}</span></div>
<h2>裁决规则</h2><ol>{rules}</ol>
<h2>附则留痕（两版全文随包导出，不丢失）</h2>{annexes}
<h2>逐项目组合依据矩阵</h2>
<table><thead><tr><th>样本</th><th>批次</th><th>项目</th><th>采纳报告</th><th>采样日</th>
<th>报告日</th><th>适用附则</th><th>限量</th><th>冻结结论</th><th>若整体换新</th><th>裁决码</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="noprint">浏览器打印可另存为 PDF；JSON 证据包请从快照管理页下载。</p>
</body></html>""".format(sid=snapshot['snapshot_id'],rid=snapshot['run_id'],status=snapshot['status'],
        frozen=snapshot['frozen_at'],gb=document['national_standard'],dgst=document['document_digest'],
        rules=rules,annexes=annexes,rows=rows)
