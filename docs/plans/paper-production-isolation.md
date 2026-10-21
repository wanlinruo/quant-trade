# Paper 独立生产迁移（#490 / #330）

## 当前事实与边界

生产Paper仍从共享checkout/venv以Ubuntu运行；2026-10-22凌晨只读快照为两个running、
一个ended模拟账户，历史17条模拟订单/60条模拟成交，当前持仓行0。
这些是模拟状态，不代替真实交易账户证据，也不把归档持仓候选数量等同于当前Workbench持仓。
实际cgroup观测内存峰值624MiB、swap峰值511MiB，不能把批处理CPU50%直接套给盘中调度。

新身份 `qsys-paper` 私有主组，补充组仅 `qsys-observers`；Workbench通过精确named user ACL写入。
真实探针发现qsys-workbench还可写var/runs，因此不把Paper加入该写组。
Workbench确实由Web、worker和Paper共同写，保留实际owner=qsys-web/group=qsys-workbench/0770，
不在Paper安装时将共享目录owner切为paper、不递归chown旧文件。
这仍是共享SQLite边界，不能宣称Workbench数据库已经按进程拆成各自单写者。
写集合只有原Workbench及 `var/alerts/paper`；Warehouse根/所有业务表、Live/Shadow状态、
其他组件记录和完整配置均不可写，完整生产配置不可读；unit另显式隐藏共享config目录。

最小配置 `/etc/qsys/components/paper/deploy.toml` 包含当前实际解析的Redis连接、
行情请求/响应及heartbeat streams、Tushare交易日历token、实际ops通知目标，显式禁用真实下单。
不复制实盘账户、资金、策略或完整部署配置；单账户通知仍按Workbench既有配置读取。
Paper继续只构造PaperBroker，不构造LiveBroker、不写真实orders/control流。
Redis5无密码loopback隧道没有原生ACL，连接能力的残余权限由#488承接，不能冒称强制只读。

初始资源为MemoryHigh1536M/Max2G/SwapMax1G、CPUWeight80，保留盘中调度CPU使用能力；
单线程Polars/BLAS、DuckDB256MiB/spill1024MiB。真实两个worker初始化与supervisor证据通过
后才切换；自然事件性能另记，不用凌晨空转冒充交易日全量验收。

## 告警路径与读取器先行

原PaperRuntime对有notify hook的账户调用相对路径 `ledger()`，WorkingDirectory迁到 `/`
后会错误写到 `/var/alerts`。新增可选 `--alert-ledger`，supervisor透传到每个账户worker，
再传入PaperRuntime；不传时保持legacy默认值。单元测试用RecordingAlertSink，验证真实本地
ledger写入、自定义含空格路径及worker传参，不发送外部消息。

新告警目录 `var/alerts/paper` 为专属账号私有组+observers只读，0770及Ubuntu兼容ACL。
旧flat JSONL全部保留，不复制到新目录造成重复，不覆盖或搬走其他进程仍在写的共享历史。
新读取器显式识别paper/shadow-core两个目录，仍保留legacy与既有观测组件目录。
**必须先发布daily-digest兼容读取器，并用实际账号/venv核验新旧账都可发现，
再发布Paper生产者**；这两个组件是必要联合发布。调用链确认Web不消费AlertLedger，
它只做既有Workbench接口/状态兼容验收，不重新发布。Shadow只是日报读取器预先兼容，仍保持inactive。
共享告警文件验证原内容前缀保留，允许其他进程正常追加，不以整个文件hash不变阻止正常告警。

## 隔离与正式验收

使用SQLite只读连接的backup API获得一致性真实副本（覆盖WAL），不复制正在写的裸DB文件，
不对生产DB恢复/覆盖。验证副本显式关闭ops和每账户notify hook，使用独立Redis namespace，
不能让相同账户consumer group操作生产quote streams。生产Warehouse只读，组件只写验证workspace。
既有模拟资金、账户状态和订单/成交事实在原备份保持；测试副本的通知配置调整只用于防止外发。
实际supervisor及两账户子进程完成初始化、持续健康、heartbeat可读后记录资源和业务覆盖限制。
午夜不人为挪时钟触发生产模拟事件；自然09:15事件与行情读取在发布后单独验收。

发布按稳定SHA CI→真实test部署→逐文件代行自审PR→合并/自动对齐→最终制品一次下载，
核对可信白名单仅目标变化、控制器代码未变。最终候选隔离验证通过后，按
daily-digest→Paper顺序逐个dry-run/正式发布。每个必须等待workflow最终success、
GitHub Deployment与节点实际validate_record三方一致，再验证消费者或下一生产者。
Paper切换前单独重采关键peer基线，日报已经受控更新，不把它的预期变化误判为Paper越界。

Paper正式启动后检查实际User/Group/supp、父子进程都来自同一独立venv、两个running账户
状态/资金/原订单成交保留，实时heartbeat和Web可见；Live Core、Web、worker、Shadow身份不变。
其他组件不重启，timer不暂停，Shadow不因迁移启动。保留旧release、完整备份和历史，正常异常向前修复。
