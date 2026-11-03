# 前导零成交量的一致性契约（#583）

关联[#583](https://github.com/wanlinruo/quant-trade/issues/583)。维护者要求先修复指标字典失败，
再运行真实任务验证。#588/#589候选保留，未夹带进入本次指标字典修复或生产发布。

11-03 19:40真实任务完成638结果：600通过、28既有警告、10阻断，表11绿1黄、关系7绿。
10阻断来自同一B2规则的12标的日，样本与17点SHA完全相同。旧判据仅按qmt来源及连续
两根以上零量判失败，没有验证零量行与实际参考价、首个成交分钟之间的一致性。

[本轮只读实测](minute-zero-consistency-20261103.json)固定当天12例：前导2–5根均
volume=0、amount=0、四价一致且精确等于Tushare直取pre_close；首个正量分钟的open
全部精确等于Tushare当日open，日线volume均正。读取前后两个输入分区SHA保持。此处
直取列名/单位沿用已核验Tushare表，不从价格反推数据，不换单位、不舍入、不加容差。
原生False/True与入库对照及独立成交观察另见native-minute-trades-review-20261103.md。

进一步对当前raw/canonical全部5570标的日筛出73个前导非正量案例，将完整前导行及首个
正量行、同日直取参考下载到本地复放候选函数：两层各73例均0矛盾。原始只读输入、
物理版本与收据SHA在结构化摘要记录的独立持久审计目录保留，未写生产质量记录。

既有minute_rules已记录297例“集合竞价无成交时09:30四价等于昨收，开盘价取首个正量
分钟”的生产实测。连续零量的根数本身不是质量错误；新规则以值与直取事实的矛盾为
失败条件，适用于所有来源，不给北交所或qmt来源整体豁免。

本次一致性条件：前导行零量/零额/四价一致/正价格/等于直取参考价；时间键不重复；
首个正量分钟open与直取当日open一致。缺参考、null/非有限值、负量、零量正额、不平价、
参考价或首成交价不一致仍blocking。有日线成交而分钟全天无正量同样失败。
前导行不会被删除、补齐或重写；原B2规则ID与blocking严重度保留，旧红项不修改。

该规则检查值一致性，不声称已经证明每根历史数据的上游生成过程；D-23既有历史事实与
回测源禁令保持，#586数量差异继续保留。实际生产验证见下文，28项既有警告不得清除来制造全绿。


## 生产发布与实际重跑

PR #590对应5c2037f，catalog/checks的正式workflow41651261215/41651795823、Deployment
7521021097/7521102439及节点实际validate_record三方success。维护前节点恢复保护已设置，
两timer已恢复。按维护者要求启动实际catalog unit，新记录`catalog-verify-221822-942857`
638字段结果：610 passed、0 blocking、28 warning；B2十字段均passed，5570标的日0矛盾。
表11绿1黄、关系7绿/缺证据0，unit success/exit0，ServiceRun仍warning。

旧QA三表65773/1140/126行逐值保持，新增638/60/7行；旧ServiceRun字节、4555个市场文件
物理版本、KILL/主库与其他peer保持。正式收据在节点
`/data/qsys/var-deployments/issue583-zero-consistency-20261103-r1/task-verified.json`，
Mac持久审计目录`~/.local/share/qsys-audits/issue583-zero-production-20261103-r1/`保存副本。
这不是改写旧红转绿；同期17/19点同步与19:40/20点旧任务记录保留。

必要消费者tushare-sync亦独立正常发布同一5c2037f：workflow41653138392、Deployment
7521307183及实际节点record三方success；timer恢复，原终态/12peer/4558个Parquet版本
与KILL/主库保持，未重跑采集。实际UID983的安装模块内存正反夹具、增量摘要和源码SHA
核验通过；不能替代下一次自然同步的业务验收。审计在
`/data/qsys/var-deployments/issue583-zero-sync-20261103-r1/`。

安装后只读探针首版误用`/opt/qsys-data/tushare-sync/current`，二进制未启动；首版空输出保留。
按节点正式record定位`/opt/qsys/components/tushare-sync/releases/5c2037f...`后r2探针通过，
未重放发布或业务。r2模块SHA与git源码精确相同，UID983、仓库写入0、业务启动0。
