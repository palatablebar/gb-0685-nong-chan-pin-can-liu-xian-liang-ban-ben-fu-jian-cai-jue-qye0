# 农产品残留限量版本与复检裁决

面向农产品检测站复核员，处理采信、判定、核算、复核与归档。

## 启动

```bash
docker compose up --build -d
```

访问 http://localhost:18254 。初次启动自动运行脏样例，可切换 clean.csv、legacy.csv 与 transition.csv。顶部“运行批次”会重新计算，同源输入复用批次。

## 业务模块

- 报告采信：`/m1_ingest`、`/api/m1_ingest`
- 项目映射：`/m2_mapping`、`/api/m2_mapping`
- 限量选版：`/m3_versions`、`/api/m3_versions`
- 检出量换算：`/m4_segments`、`/api/m4_segments`
- 复检裁决：`/m5_calculate`、`/api/m5_calculate`
- 批次判级：`/m6_reconcile`、`/api/m6_reconcile`
- 报告归档：`/m7_archive`、`/api/m7_archive`
- 依据裁决：`/m8_snapshot`、`/api/m8_snapshot`（封存/导出 `/api/snapshots/*`）

## 附则换版裁决（M8）

国家标准 GB 2763-2021 不变，报告只写总标准编号；地方附则按**采样日**锁定，初检与跨生效点晚到的复检同属一个采样日，适用同一附则，新附则不溯及既往。M8 页面逐条给出组合依据矩阵与"若整体换新"的反事实结论（`RETEST_SHIELDED` 标记被保护的已确认项目），封存后形成标准快照：

- 项目结论、批次判级、归档出口只认 ACTIVE 快照；未冻结或批次不匹配，证据导出返回 409。
- 封存是单事务：中断（页面"中断演练"按钮）整体回滚，旧活动快照保持有效。
- 同源重跑返回同一快照、不新增；被替代快照转 SUPERSEDED 但正文、两版附则全文、原始报告与审计链永久保留。
- 每份快照可导出 JSON 证据包（含两版附则全文与原始报告）与可打印 HTML 复核页。
- 跨附则场景样例：`transition.csv`（在运行批次中选择，或 API 传 `{"sample":"transition.csv"}`）。

## 核心规则

按采样日期选择有效限量，低于检出限保留区间；复检不得越过样本关联与方法适用范围。完整数值、边界和异常定义见 `docs/DATA-DICTIONARY.md`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

原始样例在 `sample_data/`。测试在 `tests/`。封存文件是单份CSV，含运行清单、依据快照行、计算证据、隔离原因与对账。复核需提交当前版本，陈旧复核返回409。标准快照证据包为单份JSON，附可打印HTML复核页。

## 数据与限制

SQLite保存在records命名卷，普通重启与构建不会清除。确认可丢弃本项目数据后，运行 `docker compose down -v` 再启动可重置。单文件上限2MB；不接入外部业务系统；历史规则由本项目样例定义，不代表实际行业标准。
