# QMT 恢复优先级与夜间排障（2026-10-26 至 10-27）

承接 [#533](https://github.com/wanlinruo/quant-trade/issues/533)、
[#540](https://github.com/wanlinruo/quant-trade/issues/540)、
[#543](https://github.com/wanlinruo/quant-trade/issues/543)、
[#544](https://github.com/wanlinruo/quant-trade/issues/544)。时间均为 Asia/Shanghai。
维护者明确授权按评估方案继续排障和可恢复调整，仍保留真实交易、数据覆盖及质量门禁。
本记录不包含账号、配置正文、截图、凭据或原始账户日志。

## 1. 恢复顺序与当前数据事实

最小数据保底比完整实盘恢复更快；全市场分钟/竞价恢复没有已验证正式上游，不能假设它更快。
保住 Tushare 日线底座，优先排除客户端不可用和监控读取故障，再完成策略所需的最短实盘闭环。
全市场归档回补后置，但策略依赖的竞价、实时行情、历史窗口与风控不能省略。

| 10-26 自然运行 / footer | 结果 | 限制 |
|---|---|---|
| Tushare 19:00 | 日线直取 5570 行，年度文件 1068569 行、最大日期 10-26 | 整体 warning，不是全部质量通过 |
| 每日归档 15:40–17:23 | 分钟下载超时 6000 秒；进度 2988/5570、失败 2988、落盘 0 | 有行情服务连接失败，不单独归因 SSH |
| 两张分钟表 10-26 分区 | 均不存在 | 行数记未知，不能造 0 行或补 bar |
| 竞价年度表 | 275270 行，最大日期仍 10-23 | 不代表当日到货 |
| 20:00 到货检查 | 分钟缺 5570 股，候选竞价缺 72 项 | 真实缺口保留告警 |
| 增量质量 / catalog | auction_ticks.dt / B4.arrival 阻断 72 项 | 不改门禁或放宽阈值 |

`qsys-issue475-natural-audit.service`、`qsys-issue515-daily-archive-natural-audit.service`
是 10-22、10-23 的历史失败，不能算成今天新增故障。Live 按授权停止属计划状态；
主库读取失败、新客户端启动异常则需独立处理。进程健康不等于行情新鲜、数据完整。

## 2. 归档定时触发临时暂停（#540）

23:49 重新确认归档 MainPID=0、旧 Live MainPID=0 后，仅执行一次
`systemctl stop qsys-daily-archive.timer`。验后 inactive/dead，原 enabled 保留；
归档原 failed 与历史记录保留。Tushare timer active/enabled、日线自然任务保持。
未补采、切源、覆盖数据、关闭质量任务或修改源选择。

审计：`/data/qsys/var-deployments/issue540-20261026-archive-pause/pause-record.json`。
这是当前开机期间的暂停，节点重启会恢复 enabled timer。恢复前应确认正式上游可用、
小范围实际到货及质量通过，再恢复定时触发；不能直接重放失败的 6000 秒全市场下载。
归档与日线窗口重叠是风险，尚无证据认定它造成日线失败。

## 3. 停机后状态库读取（#544）

真实主库仍在，停止 Live 后 WAL/SHM 已不存在。实际 qsys-service-checks UID 以 mode=ro
读取复现 READONLY，原只读 systemd 沙箱报告 CANTOPEN。这是状态读取故障，不证明当日
发生了新的委托或策略执行失败。不能启动旧 Live、创建空库、放宽消费者目录写权限或用
immutable 绕过 WAL 来掩盖。

隔离 fixture 使用实际生产者/读取者 UID：冷库只读失败，生产者身份只读 URI + query_only
辅助连接存在时，真实读取 UID 和 ProtectSystem=strict/ProtectHome=read-only/PrivateTmp
沙箱均读通；业务 INSERT 拒绝，fixture 主库 SHA 不变。

23:51 启动临时 `qsys-issue544-state-reader-anchor.service`，只运行独立 SQLite 只读连接，
不导入或初始化交易 Runtime、Redis、通知或 LiveStore。真实 checks/observer UID 在只读
沙箱各读取成功：70 条本地历史事件、0 条本地 orders。计数不是券商对账或空仓证明。
真实主库 SHA、mtime、owner/group/mode 不变，WAL=0 字节、SHM=32768 字节。
自然 observer 已从 strategy_state_unavailable 转为 strategy_event_failed，不能据此报 healthy。
今日自然 service-checks 的原失败未重跑、删除或覆盖。

审计：`/data/qsys/var-deployments/issue544-20261026-reader-anchor/` 下
`fixture-result.json`、`activation.json`、`anchor-readonly.py`。
辅助源码 SHA256：`474aa9c7d18df259420ce97aa4262935c06df9358e569905006e5b83510c53a2`。
真实主库 SHA256：`a44f1befac5254cd65d8f905ec8fae0081eac418e86f0c7be421cbd498fd5190`。

**本项仅临时缓解，#544 不关闭。** unit 与脚本最长运行 86400 秒，约 10-27 23:51 到期，
节点重启亦不会自动恢复。到期前须复核接管进度与正式维护方案，不盲目重放 activation。
永久停机态读取及维护状态展示仍需正常开发、验证与发布。

## 4. 新客户端单次受控恢复（#543）

21:45:28 Windows Application Event1000：XtItClient.exe 2.1.33.0，模块 ucrtbase.dll
10.0.14393.9060，异常 c0000409、偏移 000000000006de4e。原 PID2712 停留重新初始化页面，
Crashui5436 存在。Lua nil/customModules 缺文件日志不证明故障根因，未修改配置或替换 DLL。

23:52 精确核对根目录、PID、启动时间、session、唯一客户端及四个原 task XML 后，单次
停止这两个异常进程，从新版 bin.x64 工作目录启动原 exe。CreateNew 维护 marker 禁止重放。
新 PID5452/session1，23:52:42 启动；只读窗口截图确认进入登录后模型研究，原诊断模型存在。
公告已关闭，未改“不再提示”等设置。原四任务 XML 不变，三个辅助任务 disabled。
未更改 QMT 自动恢复、密码、RDP、账户或交易配置，没有下单、撤单或 KV 调用。

Windows 私有审计沿用 `C:/qsys/var-deployments/issue533-20261024-qmt-install/`，包含
`client-startup-restart-requested-20261026.json`、`client-startup-restart-result-20261026.json`。
原始日志和截图不入 Git/Issue。一次重启恢复界面不等于根因修复或长期稳定性通过。

23:57 尝试打开只读编辑器时，前台守卫在输入前拒绝；三个辅助 task 已禁用，未继续尝试
绕过窗口守卫。新版客户端保持运行，等待可交互前台后继续沙箱验证。

## 5. 实盘剩余关键路径

1. 在当前实际新客户端只读查询旧诊断键，独立验收跨客户端持久性；没有新读回前保持未知。
   已准备日期限定为 10-27 的单 GET 探针，源码 SHA256
   `52dd42185c3cf521e812c06a03eeab1357bc42c1ce6fa103eefee988f9feae86`。
   Python3.6 语法解析及离线成功/空值/错误码/异常四路径通过，每次只读一次。
   **尚未上传或执行**，离线结果不作原生持久性证据。
2. #542 优先确认首次恢复日及必要滚动窗口的股票池、历史限价、停牌、ST 与 Gate 输入。
   当前沪深A股历史名单为空不能由格式更换、当前名单回填或空候选来“解决”。正式板块目录
   API 可用于发现原生板块名称；get_sector 文档仅支持指数历史成分，不盲目作全A回退。
3. #541 完成真实账户新鲜度、持仓与活动委托、pending、逐lot、成交标识及去重闭环。
   19 字节诊断值读写成功不证明容量、事务原子性、多实例或完整账本恢复；不扩写为 5000 字节。
4. 若维护者有已正式开通且当前可用的其他券商接口，比较复用既有策略/账本的整体成本。
   尚未取得该信息，不假设其他券商/平台天然可用，不开账户、转资金、购买或实盘试单。

代码合入、CI、测试部署、原生样本通过与真实交易激活仍分别记录。旧 Live 保持停止，
KILL 与资金上限保留，未激活新版交易。

GitHub 评论请求曾在直连及本地代理上超时；HTTP 超时不证明服务端未写入。
补记运维结果前按完整正文精确匹配已有评论，避免重复 POST；本文件作为可审查交接留存。
