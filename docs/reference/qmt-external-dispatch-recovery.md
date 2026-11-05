# 外部薄执行器的发送状态与恢复契约（#599）

本页是设计及离线诊断原型，不是已接入交易的实现。关联
[#599](https://github.com/wanlinruo/quant-trade/issues/599)、#541、#535。
11-04完成原生编辑器内内存去重及Mac离线账本测试；11-05已在Windows私有夹具接入QMT
编辑器接收器并完成两阶段恢复诊断。尚未接入生产网关、真实意图或券商接口。

11-05 11:00后续：[#601应用隔离接线](qmt-async-dispatch.md)已把实际AsyncOrderDispatcher
及其SQLite账本接到同一QMT诊断协议，补齐ACK后、有效期内跨接收会话的独立对照。
旧意图零重发，新对照ping成功；具体时间与边界见
[机器摘要](qmt-async-qmt-probe-20261105.json)。此证据不追溯改变下文原型当轮的过期限制，
也不证明客户端重启、真实信号或订单回报已接通。

## 为什么不能直接替换现有BrokerAdapter

现有`src/qsys/agent/adapter.py`要求`place_order()->str`返回真实券商订单号。
`execution_agent.py`用`find_order(client_order_id)`先查已存在委托，再经`IdempotencyGuard`
调用适配器并记录返回值。这个查询步骤有价值，不能略过；但“查询未找到”不等于已证明
未知发送从未被柜台受理，查询完整性、可见延迟、时间范围及唯一关联仍待验证。

`IdempotencyGuard.process_once`在handler返回后才记录结果，异常不记录。新异步QMT通道
不能把空返回、传输ACK或本地请求编号塞进这个同步接口；也不能把超时异常直接当成
确定拒单，否则可能重发已受理意图或把未确认结果错误地标为失败。当前不修改、启动旧Live。

## 所有权与状态

Linux保留策略lot/FIFO、行情真值、风控与意图真值；Windows专用网关持有持久发送尝试与
实际会话身份，QMT只处理白名单操作及真实回报。代码发布不能复制旧业务库覆盖新事实。

| 状态 | 事实含义 | 自动重发 |
| --- | --- | --- |
| prepared | 意图已持久化，尚未取得发送尝试 | 有效期及所有门禁通过后，原子领取一次 |
| dispatching | 发送尝试已提交，后续可能发生外部效果 | 禁止因超时或重启重发 |
| transport_received | 对端接收ACK已记录 | 仅传输事实；不是券商受理，仍禁止盲重发 |
| unknown | 可能已发送/受理，证据不足 | 停止推进，查询对账，不能改回prepared |
| broker_accepted | 有真实券商订单号及匹配回报 | 只继续回报对账，不重发 |
| partial/terminal | 真实部分成交、成交、撤单或拒单事实 | 按真实事实处理，不由超时推定 |

发送前必须先取得真实单实例锁/实例租约并验证当前KILL、账户绑定、时间、证券和额度。
随后在外部持久库提交attempt_id/session_nonce，再发布消息。进程在“提交尝试→实际发送”
间退出，即使实际上可能没发，也必须保守进入unknown；不能根据日志缺失猜未发送。
数据库与券商不能共享原子事务，不能承诺仅靠本地去重实现端到端exactly-once。

恢复需要先独占实际所有权，读取未完成尝试与真实券商活动委托/成交/拒单/撤单事实，
校验字段覆盖、源时间、交易日及唯一关联。查询空值、旧快照、超时或无完整性证明时继续
unknown，不能解释成空仓/无单。不从聚合持仓反推策略lot。柜台回报已受理后再发生断线，
仍用真实ID推进原尝试；不生成新意图来规避未知状态。查询完整且证明未发送的处理规则
须另行验收，当前诊断原型故意没有unknown→prepared转换。

## 离线诊断原型

`qmt-external-dispatch-ledger.py`只接受显式标记的私有fixture目录与固定合成文本。POSIX目录
0700、数据库0600；Windows直接核验实际NTFS DACL、路径无reparse及数据库继承权限，
只允许当前SID/SYSTEM，不以chmod位证明权限。不含账户、证券或下单调用；逐操作SQLite
事务、FULL同步、确定性关闭：

- 不可变消息ID/指纹；同ID改时间或内容拒绝。
- prepared→dispatching按条件更新原子领取，两个独立连接只能有一个成功。
- attempt_id绑定传输ACK；仅记录transport_received，不冒充柜台受理。
- 进程重新打开后，dispatching/transport_received→unknown，原attempt/会话和ACK保留，
  unknown无法重新领取。诊断echo完成后保持diagnostic_echoed，不称其为订单终态。

`tests/scripts/test_qmt_external_dispatch_ledger.py`用真实临时SQLite和子进程`os._exit(23)`，
分别覆盖提交发送标记、提交传输ACK后的退出点，再冷打开验证unknown及禁止重领；另测
连接竞争、过期、ID冲突、错误attempt与错误响应SHA。没有生产故障注入、节点重启或业务库访问。

这些离线测试只证明小范围状态不变量；11-05又补充了下述Windows隔离接线，仍未验证掉电、
生产状态迁移、故障磁盘及真实券商查询。原型不接受真实订单，不能注册为生产组件。

## 11-05：Windows持久账本与QMT编辑器的两阶段接线

[机器证据](qmt-external-recovery-probe-20261105.json)绑定exercise、两个会话、源码SHA、
Qt输出、子进程收据、冷库SHA和资源验收。外部使用已安装的Python3.12.10/SQLite3.49.1，
没有安装依赖。`qmt-external-recovery-coordinator.ps1`在私有夹具持有真实FileShare.None锁，
然后创建新命名映射并启动`qmt-external-recovery-gateway.py`诊断驱动及隔离子进程。
二次独立打开同一锁得到Windows错误32（sharing violation）；正常结束后独占打开成功。
此锁只约束本次fixture，不代表生产交易全局单实例/分布式fencing已验。

| 消息 | 第一阶段的真实诊断动作 | 持久结果 |
| --- | --- | --- |
| A | 发布→验证Qt echo→写ACK→诊断完成 | diagnostic_echoed |
| B | 提交发送标记并发布后，当前诊断子进程os._exit(23) | 冷打开转unknown；原attempt/会话保留 |
| C | 验证echo并提交传输ACK后，当前诊断子进程os._exit(23) | 冷打开转unknown；原ACK SHA保留 |
| E | Qt接收器处理完三条后自行取消/关闭，随后发布E但没有当前generation回报 | 等待2秒超时，转unknown；不把旧C回报当E的ACK |

三次unknown重领均在第一阶段有效期内被拒，未再次打开/写入传输。Qt实际收到A/B/C三条，
收到B的echo这一诊断事实另留收据，不能将其升级为真实券商受理或改写unknown。
第一阶段实际发布4次（含未获回应的E），Qt接受3条测试文本后主动取消本组。

第二阶段在同一QMT客户端重新初始化另一诊断模型，使用新的wire会话与空内存缓存；
原客户端PID5480/start=11-04 20:54:17.9225309保持，没有重启QMT或Windows。
外部仍读原SQLite，A/B/C/E均未重新发布，只发送新的D，Qt接受D的seq1后自行结束。
最终A/D为diagnostic_echoed，B/C/E为unknown；A/B/C/E整行逐项与第一阶段结果完全一致。
**第二阶段旧消息已经过期**，因此该段没有隔离过期条件，不能单独证明有效期内跨接收会话防重。
有效期内unknown禁止发送的Windows现场证据来自第一阶段，跨会话有效期内的独立场景仍待补验；
本地单测另验证了新session_nonce遇到未过期unknown时不触及传输。

两段Qt实际处理3+1条，组取消均true；最终callback_bound记录最大耗时约1.2955/2.5620ms，
不是性能承诺。finished事件在最终finally计时前输出，不能拿其第二段0ms当最终测量值。
4组echo规范响应由维护端独立重建HMAC/SHA，与收据精确一致；签名仍用公开fixture key，
不代表生产身份认证。各子进程退出仅作用于新建诊断进程，没有停止客户端或注入生产服务故障。

01:20同session1检查两映射均不存在，独占锁释放，两个驱动exit0，q0~q10全exit0/disabled/idle，
审计q11随后也正常禁用；四业务任务XML及QMT身份保持。103个新审计文件逐项确认允许项仅当前
SID/SYSTEM。随后真实只读SQLite冷读quick_check=ok、journal_mode=delete、5行结果一致，
前后SHA完全相同。没有使用immutable忽略WAL，没有复制旧业务库。

首次Windows建库烟雾测试的外层汇总脚本因占位符误替换而failed（CommandNotFoundException），
其业务stdout已为success/prepared；失败保留，另取只读冷库证据，未重跑建库。两阶段正式诊断
均使用新run和明确收据完成；不将旧辅助失败改写成成功。

新增三项本地专项覆盖错误会话/消息回报拒绝、未过期unknown在新会话写入前拒绝及NTFS校验
失证拒绝。连同既有四组，共65项通过。现场证据不代替生产身份、实盘模型模式、完整客户端
重启、网络断开、掉电持久性或真实柜台对账，#541/#542不因诊断完成关闭。

## 下一阶段

1. 确认券商允许的接入方式，以及无账户诊断能覆盖到何种正式运行模式；不得为此选择真实账户。
2. 设计实际进程身份、私有密钥分发/轮换、单实例与租约fencing。签名测试用公开fixture key，
   不代表身份隔离，不能把测试密钥写进生产模型。
3. 已完成一轮隔离账本/接收器接线；补有效期内跨接收会话及实际进程身份/租约验证，再验证
   完整客户端重启和通信故障。明确ACK层级，不接真实意图Stream，不改只读行情Gateway权限。
4. 获得当前账户正式许可及真实回报证据后，才进入独立的真实闭环与激活审批；北交所禁开仓、
   急停、单一交易所有者与#589最后一跳限制继续生效。

锁与落盘API参考：[FileShare.None](https://learn.microsoft.com/en-us/dotnet/api/system.io.fileshare?view=netframework-4.8.1)、
[FileStream.Flush(Boolean)](https://learn.microsoft.com/en-us/dotnet/api/system.io.filestream.flush?view=netframework-4.8.1)。
这些API说明不替代本次现场锁测试，也不证明掉电持久性。
