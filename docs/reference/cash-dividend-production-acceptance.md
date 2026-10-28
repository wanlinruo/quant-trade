# 八条历史现金修复生产验收（#558 / #540）

维护者已明确确认[八项审阅包](https://github.com/wanlinruo/quant-trade/issues/558#issuecomment-6474247448)。
2026-10-29 00:22:21 取得真实 warehouse-write.lock 并重核基线，00:22:54 原子替换唯一事件
文件，00:23:21 完成复验。此次批准范围已经执行，无需再次请求同一覆盖的确认。

## 数据与消费者验收

- `corporate_actions` 仍为 61085 行，仅[原审阅表](cash-dividend-announcement-review.md)
  中八个 `cash_per_share` 改为公告独立核验的 Tushare 直取值。所有其他单元、键、顺序、
  Arrow schema 和 metadata 精确保持；owner/group/mode/access ACL 精确保持。
- 原 SHA256：`eab786c3a2de0a300c832a3b53749bcd33f6d7356f9b1ab6b47b28dce0b4dee5`。
  现生产 SHA256：`df021519cad842db2cf1f5b470b9af3ce3ac1bf00498b76531fb13c177bcab43`，
  与批准候选精确一致。原文件由独立 inode 的 `cp --archive` 副本保留并验证原 SHA。
- 替换前，新 inode 由 producer UID983、backtest986、Paper979、Web990、checks993、
  digest988 六真实身份在只读沙箱读取，全部通过；替换后同六 UID 调用实际
  `WarehouseQuery.corporate_actions()`，全表与候选逐值精确相等。共十二份 UID/PID/
  Invocation 回执，没有启动任何业务 Runtime。
- 两张因子文件字节保持：adj_factors SHA256
  `09dab004e256fb2a5b0a47ca4224279308804136d22e26fc372d794088153672`；canonical2026
  `44ffce3d299ca2ef4d00de4eab964339175e7129f38be72960f5ecc48208afd5`。
  没有重写因子表、调整算法或重标阈值。
- KILL、Live 主库字节/inode/ACL、自动送配 checkpoint、组件3facceb、共享 checkout505d7e8
  及索引、六 peer 的 PID/状态/NRestarts、原归档/Tushare/自然采证 timer 全部保持。
  Live PID0，Paper634053、Web3700671、observer3933655、reader1466393、backtest1300978。

## 执行与留证

实际维护 PID2250665/UID0，invocation `2dbe79efdcfd4562a1f4d6f5c86006c9`，unit
`qsys-issue558-cash-approved-r2-20261029`，exit0/success，MemoryPeak159592448 字节。
一次性 root 只用于保持 ubuntu 属主和精确 ACL；资源/查询环境取采集契约，写路径限定
事件目录和审计根，禁止网络取数。实际运行时属性已留证，不以辅助 unit 启动成功代替
业务文件和真实消费者验收。

首次辅助任务在只读 Git 账号切换步骤被沙箱拒绝，requested=false/replace=false，生产
字节保持。先以维护账号核对 Git，再只读比较固定 HEAD/ref/index，未放宽 safe.directory
或沙箱；修复使用新 r2 run-id，首次失败 invocation745e3cf...和原记录完整保留。
00:24 已停止成功的一次性 timer/service；停止后 unit not-found 不替代此前真实运行证据。

成功审计根 `/data/qsys/var-deployments/issue558-cash-approved-r2-20261029/`：
apply-requested.json、locked-validation.json、before/after.json、actual-running-contract.json、
prepared-and-readers-verified.json、atomic-replace-completed.json、十二份 reader 结果、
apply-result.json、verification-summary.json、maintenance-finished.json及原文件备份。
首次失败在 `issue558-cash-approved-20261029/`，不得清除或重放旧 requested。

## 剩余范围

[原结构化审阅记录](cash-dividend-announcement-review.json)保持审批时内容，其
production_writes=0 描述的是候选准备阶段，本文件记录之后的正式执行。
未来新事件的现金选源尚未改变，另八条 null/0 继续保留源值；#558 仍跟踪该剩余事项。
#540 继续自然增量和其他数据问题，模拟/实盘未恢复，KILL 未清。

10-29 17:20 自然只读采证 timer 仍 active。其脚本没有绑定旧事件 SHA，已核对无需调整；
当天自然业务尚未发生，不能用本次历史修复替代自然任务验收。午夜未重跑按日采集或质检。
