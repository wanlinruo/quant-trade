# 外部决策与大QMT薄执行器可行性（#599）

承接 [#599](https://github.com/wanlinruo/quant-trade/issues/599)，关联 #533 / #137。
2026-11-04 维护者授权继续评估外部计算信号、由大QMT执行的路线。本页记录诊断阶段，
不作为生产接入协议或实盘就绪证据。

## 已有证据与未完成项

| 层级 | 实际证据 | 结论边界 |
| --- | --- | --- |
| Windows进程token | 19:35旧客户端PID4968：AppContainer=false、restricted=false | 仅主进程两项属性，不能代表策略运行环境/模块限制/对象权限 |
| 外部Windows进程通信 | 两独立PowerShell进程PID3600/2492，命名管道5帧、单向5181字节，双向SHA一致、对端PID互证 | 未从QMT发送或接收；SID ACL不等于应用层认证 |
| 诊断协议离线测试 | 28项专项测试通过 | 无真实传输、无持久化、无账户，不证明生产可用 |
| QMT内模块能力 | 22:17编辑器模拟运行：Python3.12.10，json/socket/ctypes/mmap导入成功，WinDLL属性存在；win32pipe/win32file为ModuleNotFoundError | 未调用DLL、网络或文件，导入不等于相应I/O可用 |
| QMT内收发 | 22:24标准mmap命名共享内存：QMT读248字节请求、写216字节响应；外部端逐字段校验，独立重建响应SHA一致 | 单次固定文本、编辑器模拟模式；非正式运行回调、无应用认证/持久去重 |
| 正式策略运行模式 | 尚未验证 | 编辑器结果不能代替实际运行回调证据 |
| 当前券商与账户接入许可 | 尚未取得正式确认 | OS能力与公开教程不能替代账户权限 |
| 交易受理/回报/重启对账 | 尚未验证 | 未调用账户/下单/撤单；未恢复Live/Shadow |

命名管道固定样本包括ASCII、UTF8中文、全部字节值、4096字节上限以及重复帧。
管道ACL只允许当前Windows SID及SYSTEM，逐端记录实际PID、对端PID、长度和SHA。
客户端仅连接本机；未验证远端管道访问拒绝，不能作为生产通信安全验收。
外层汇总启动器在两端成功后仍报failed，原因尚未证实，原失败保留；独立读取两端收据、
源文件/样本SHA及四个业务任务XML后对账成功。不能把独立对账表述为原启动器成功，
也不为修饰终态重发已经完成的消息。

## 22:24：QMT内双向共享内存诊断通过

当前结论：可以继续推进外部决策与大QMT薄执行器原型；“外部程序与当前QMT完全不能交互”
已不符合本次编辑器实测。不能推广为任意接口可用、券商许可或可恢复实盘。
机器摘要见 [11-04分层证据](qmt-external-signal-probe-20261104.json)。

- 22:14重新读取编译日志，确认旧批次21:09编译成功；旧运行任务失败仍保留，没有重放。
  新能力批次`c5e3e71061d24b45a813d28d0e621a46`在22:17取得9条完整协议输出，
  客户端2.1.33.0、实际Python3.12.10，不修改运行库配置。
- 新独立模型`QSYS599_MMAP_20261104_A`使用Python标准`mmap(-1,4096,tagname=...,
  access=ACCESS_WRITE)`，没有ctypes调用、原生DLL加载、hook修改、文件读写或网络连接。
  仅接受固定`QSYS599_DIAGNOSTIC_ONLY`，7字段请求严格绑定nonce/请求ID/到期；
  init只读一次、写一次响应并关闭，无循环、定时器或等待；handlebar为空。
- 外部PowerShell进程2792在相同交互session1创建4096字节非文件映射，DACL仅当前SID与
  SYSTEM且关闭继承。只持有一个请求，等待有界125秒；QMT侧没有阻塞等待。
- 22:24:01两端同run_id=`b716169a5e8b4ccdb809046b819e2f2a`、
  request_id=`39e08161d8a5426c974870ff356baf9e`。QMT协议输出echo_written，
  请求248/响应216字节；外部端确认固定文本和字段，维护端按协议独立重建216字节响应，
  SHA256=`983dff4c97142055857d36a00eb3ee13c6dd91e7f2b68dd02cf10203dd6d6b7d`精确一致。
  外部等待18.363秒包含人工/GUI准备时间，不能当作通道延迟或性能指标。
- 首次m1因误用不存在的MemoryMappedFileAccessRule类而在创建映射前失败；a21由就绪
  守卫拒绝，未发送运行按钮。按节点真实AccessRuleType/AccessRuleFactory签名修复，
  先在Windows验证两条受保护规则，再用新m2取证，原失败保留。
- 22:26在同session1以OpenExisting只读检查映射已不存在，外部2792已退出；本轮14个
  辅助任务全部disabled/idle，原UI任务disabled，四业务任务XML逐项保持；QMT5480/start
  20:54:17.9225309及Responding=true保持。独立审计任务a24随后正常退出并禁用。

此测试没有传入证券、市场数据、订单或账户信息，没有查询账户或调用下单/撤单。
同SID下其他进程仍可能访问此映射，nonce不是认证；没有验证正式回调、持续队列、重复/乱序、
跨日/重连/崩溃恢复、实际对端进程强身份或任意模型参数的生产权限。一次成功也不能证明
稳定性。诊断模型不连接真实意图流，不作为生产接收器。

## 23:20：有界调度回调与8条消息组合验证通过

新独立模型`QSYS599_QUEUE_20261104_A`在编辑器模拟环境中调用正式`schedule_run`，
使用独立任务组、1秒间隔和有限repeat参数24，每次最多处理一条稳定generation消息。
没有在init循环消费、sleep或阻塞等消息；8条处理完主动cancel本组并关闭映射。
实际任务4123，8次回调，cancel_result=true，最大回调耗时3.5119ms。此数值仅是本次8次
样本的测量，包含日志/取消/关闭，不是性能保证或交易时刻精度验收。
机器证据见 [11-04队列组合诊断](qmt-external-queue-probe-20261104.json)。

| generation | 输入 | 结果 | 累计接受的不同测试消息 |
| --- | --- | --- | --- |
| 1 | 有效seq1 | ok | 1 |
| 2 | 相同请求重复 | 返回原始响应字节 | 1 |
| 3 | 错误签名seq2 | authentication拒绝 | 1 |
| 4 | 有效签名但seq3提前 | sequence拒绝 | 1 |
| 5 | 有效签名但seq2过期 | expired拒绝 | 1 |
| 6 | 有效seq2 | ok | 2 |
| 7 | 同ID改内容并重新签名 | request_conflict拒绝 | 2 |
| 8 | 有效seq3新ID | ok | 3 |

外部进程5740/session1逐条核验响应HMAC、字段、计数与重复响应字节；维护端独立重建
8组规范化响应及HMAC/SHA全部一致。签名密钥是明确公开的测试fixture（32字节十进制17），
不是生产凭据，没有证明真实进程身份、密钥分发或认证隔离。所有输入仍为固定合成文本。
该传输是受控单生产者/单消费者的单槽请求/响应，不是通用持久队列。generation前后校验
不代表并发内存屏障/互斥或编号回绕已验；1秒仅诊断周期，不是订单时延配置。
QMT内去重缓存仍只在内存，重启后不保持；另有[持久发送状态与恢复原型](qmt-external-dispatch-recovery.md)，
只在Mac临时SQLite完成连接竞争和两处进程退出测试，尚未与此回调接线。

23:25同session1只读确认映射不存在、5740退出，q1~q5任务exit0/disabled/idle，q6审计随后
同样正常禁用；四业务任务XML保持、QMT5480同启动身份/Responding=true。
新审计目录保护DACL，q1~q5的35个文件逐项确认仅当前SID/SYSTEM允许访问。
旧审计父目录继承BUILTIN Users读取权限的问题由[#600](https://github.com/wanlinruo/quant-trade/issues/600)
独立承接，未递归修改或删除历史证据。

新增准备件为`qmt-external-queue-probe.py`、`qmt-external-queue-server.ps1`、
`qmt-external-dispatch-ledger.py`，均在docs/reference，不是生产接收器或应用入口。
新增回调/协议16项、离线账本5项，与既有两组41项合计62项专项通过。

## 11-05：持久账本与编辑器接收器恢复接线

[恢复接线与限制](qmt-external-dispatch-recovery.md)及
[机器证据](qmt-external-recovery-probe-20261105.json)记录Windows实际SQLite、私有NTFS ACL、
独占fixture锁、两处诊断子进程退出、接收器关闭后的无回报，以及新会话中的原账本保持。
第一段未过期unknown重领均被拒；第二段旧消息已过期，只证明旧记录保持及未再发布，
有效期内跨接收会话这一独立场景仍待补验。两阶段分别接受3/1条固定文本，最终2完成/3unknown。
这推进了隔离恢复链路，不代表实盘身份/许可、完整客户端重启或柜台受理已验。

## 诊断文件

- `qmt-external-capability-probe.py`：Python3.6兼容的单次诊断。明确绑定客户端版本、
  当天日期与非秘密32位run_id；只导入json/socket/ctypes/mmap/win32pipe/win32file，
  报告接口是否存在和脱敏错误类型。没有文件访问、联网、DLL加载、IPC或账户调用。
  模块导入仍可能被券商白名单拒绝，应保留真实拒绝，不更名模块或绕过hook。
- `qmt-external-echo-protocol.py`：无I/O的ping/echo诊断协议；严格字段/UTF8 JSON、
  拒绝重复键与NaN、会话nonce、请求ID、连续序号、显式时钟与到期、大小上限，
  重复请求返回原响应。缓存最多64条且仅内存，重启后不保证去重；不含应用认证。
  4096字节帧、256字节文本、5秒TTL都是诊断边界，不能当作交易参数。
- `qmt-external-pipe-loopback.ps1`：外部两进程的有界帧回环，不加载到QMT。
  明确配置角色/run_id/管道名/样本/收据，整体超时且不自动重试；角色收据声明无交易授权。
- `qmt-external-mmap-probe.py`：本轮标准API单次合成消息诊断。不存在外部端时可能临时
  创建空映射，空头立即拒绝并关闭，不作为通道回退；导入/映射被拒时只报脱敏阶段，不绕过。
- `qmt-external-mmap-server.ps1`：同session1非文件内存映射的临时诊断端；使用
  `AccessRuleFactory`生成真实.NET泛型访问规则，完成或超时释放映射，不是常驻执行服务。
- `../../tests/scripts/test_qmt_external_feasibility.py`：验证无业务I/O边界、脱敏、
  单次执行、配置拒绝、过期/乱序/重复/会话隔离和重启去重限制。另有
  `test_qmt_external_mmap_probe.py`覆盖固定文本、拒绝交易字段、空映射、异常关闭、
  拒绝后不重试与不回退；两组共41项通过。

私有审计保存在维护机`qsys-audits/qmt-external-feasibility-20261104`与节点
`C:\qsys\var-deployments\issue599-*`。截图可能含真实账户，禁止放入Git或Issue。
业务四任务XML逐项核对，不修改旧UI任务；新诊断任务执行后禁用，失败历史保留。

## 接入边界与实现方向

建议保留Linux数据、冻结策略、调度、策略lot/FIFO账本与风控，在执行适配边界替换通道：

Linux持久意图 → Windows专用执行网关 → 经券商允许且实测的接入通道 → 大QMT薄执行器。
委托、成交、拒单、撤单及持仓资金事实沿反向通道回到现有账本。行情数据Gateway不增加交易权限。

最终一跳仍核验身份/账户绑定、交易日/到期、证券与数量、价格、北交所禁开仓、单实例和急停。
只允许显式白名单操作；不设计任意Python函数RPC。QMT回调采用有界非阻塞轮询，
不在策略共用主线程运行阻塞监听或无界重试。

迅投`passorder`文档无返回值；现有`BrokerAdapter.place_order()->str`要求真实券商订单号，
两者不能直接对接。接收ACK只能表示接收意图，不能伪造券商受理/成交。新适配必须支持：
持久意图→发送中→真实受理→部分成交/终态；调用后断线进入unknown，先查询对账再决定处理，
不因超时重发。`userOrderId`关联字段和`strategyName`作用域须实测，不能假定柜台幂等。

若路线正式采用，#542/#538原生历史供给与#537全策略内置调度可改为外部消费输入验收；
#541仍须外部账本与真实券商状态恢复/对账，#535故障安全、#589执行拦截和单一交易所有者
仍是必要门禁。当前不关闭这些Issue，不改变数据质量或冻结阈值，不用聚合持仓反推策略lot。

## 公开资料与适用范围

- [Python审计hook](https://docs.python.org/3.12/library/sys.html#sys.addaudithook)：
  官方明确不能依靠它建立安全沙箱；这不证明QMT只用了该机制。
- [Windows AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)、
  [命名对象权限](https://learn.microsoft.com/en-us/windows/apps/develop/communication/sharing-named-objects)：
  系统仍可限制跨进程资源，“Windows内核不能限制”不是可靠前提。
- [迅投交易函数](https://dict.thinktrader.net/innerApi/trading_function.html)、
  [使用注意事项](https://dict.thinktrader.net/innerApi/user_attention.html)：
  返回/回调与共享策略线程要求；具体券商版本及账户能力另验。
- [xtquant_big_convert作者说明](https://github.com/litaolemo/xtquant_big_convert)：
  说明此架构有历史实现，但当前README已提示旧Redis/ZMQ/MySQL通道被限制、停止更新，
  不能依据旧教程将第三方桥接直接部署到现网。


标准API参考：[Python mmap](https://docs.python.org/3.12/library/mmap.html)、
[.NET CreateNew](https://learn.microsoft.com/en-us/dotnet/api/system.io.memorymappedfiles.memorymappedfile.createnew?view=netframework-4.8.1)、
[MemoryMappedFileSecurity](https://learn.microsoft.com/en-us/dotnet/api/system.io.memorymappedfiles.memorymappedfilesecurity?view=netframework-4.8.1)。
Python文档明确同名tag会打开已有映射，不存在则创建；因此必须验证真实请求头，不能仅以打开成功判断接入成功。
