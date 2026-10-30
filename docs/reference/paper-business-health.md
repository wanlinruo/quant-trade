# Paper账户错误与业务健康（#549）

关联[Issue #549](https://github.com/wanlinruo/quant-trade/issues/549)，行情依赖由#540承接，
Paper生产者SQLite异常仍由#539单独调查，维护/历史告警展示由#482承接。

10-28只读核验发现Paper supervisor仍active、心跳新鲜，但两个模拟账户已于10-26
09:18进入error；另一个账户为ended，没有running账户。原`assess_paper()`只查询running，
空集便返回HEALTHY/strategy_accounts_inactive，导致业务失败被过程存活掩盖。

准备修复首先只读统计已生效的error账户，任一个存在即返回DOWN/strategy_account_error，
说明错误账户数量并指引查看账户详情。错误原因不复制到全局健康记录，账户与通知配置
不改写；状态判断不触发重启或自动重跑策略。已生效running账户仍按原事件时刻验收，
主动paused/ended、未生效账户及无账户继续保留非运行语义。

真实SQLite夹具覆盖全error、error/running并存、午夜/盘中/盘后、暂停/结束、未来激活及
空账户；组合真实心跳评估、健康观测和本地RecordingAlertSink，验证新鲜心跳下过程仍
healthy、业务为down、告警一次且不改变账户。该测试不发送外部通知。

本修复只纠正健康判断，不能将两个实际error账户恢复为running，也不恢复QMT行情。
CI/test通过、生产消费者发布和现场生效分别验收；本轮准备不代表生产已安装。


## 10-30承接与发布边界

旧候选`bce8d70`仅留在本地`fix/paper-account-health`，并未进入a132生产main；
历史Issue中“已CI/PR”不能替代当前源码核验。本轮从最新test只承接三份运行/回归文件，
保留已合入文档及现有#482调度文案，不带入旧分支其他工作。真实SQLite回归覆盖
error与running并存、跨时段、未生效error、主动暂停/结束，组合验证进程健康而业务失败
仅告警一次，账户与数据库字节不改写。

生产Live停用时，可信v39仍拒绝普通observer发布。本轮v40仅允许已安装且active的
health-observer升级，复用可信原KILL/主库/绑定守卫，并精确保留Paper、Shadow、Web、
worker、reader、两个数据生产者、catalog和checks九peer身份。不能首次安装/启动
inactive observer，不豁免Paper或其他组件，不启动/恢复账户或Live。真实record必须调用
validate_record；正式发布仍等workflow、Deployment、节点记录三方成功，再验当前生产
UID对真实账户的只读判断。首次业务告警是纠正旧健康误报，不代表新发生账户错误。

#539两次自然SQLITE_CANTOPEN已确认，当前父目录、主库与WAL/SHM有Paper精确ACL。
当前可读写不证明故障时辅助文件/挂载生命周期正常；根因未定位前不改库、不放宽权限、
不以重复重启或恢复账户掩盖。#543的20:54进程更替单独取证，不混作SQLite根因。


## #539确定性连接释放候选

#549已由PR#576正式868b50a安装，实际observer记录process healthy/business down，
误报修复已关闭；2 error/1 ended账户继续保持，不把该关闭当模拟恢复。

实际Paper的NOFILE soft1024、数百个Workbench描述符；正常GC下Mac/Linux的2万次
隔离读取未失败，不能直接判历史两次CANTOPEN的底层原因。禁用GC的离线对照中旧代码
第509次达到1024并报SQLITE_CANTOPEN，显式GC后回到3；不是生产故障注入。
候选用事务context内部提交/回滚、finally关闭连接，PRAGMA初始化失败同样关闭。
同一禁用GC对照完成2万次且FD保持7。官方语义依据见
[Python sqlite3连接context](https://docs.python.org/3.12/library/sqlite3.html#how-to-use-the-connection-context-manager)。

单个无事务reader锚点保持WAL/SHM，不持有旧快照；对象释放或显式close会释放，
check_same_thread=False仅用于无事务锚点可由对象finalizer关闭，不共享业务事务连接。
连接关闭、提交/回滚、初始化失败和GC后只读新提交均有回归；真实UID/只读unit沙箱另验。

可信v41候选仅为已安装active Paper的无账户运行维护：任何running/scheduled（即使未来
激活）拒绝，cgroup须只有原隔离supervisor，九张Paper表主键排序流式逐值指纹、九peer及
原可信KILL/Live主库前后保持；不首次启动Paper、不暂停/恢复账户、不重放旧事件。
候选发布前先核对真实身份/权限与只读辅助文件生命周期。当前修复并不证明历史故障
唯一根因，生产安装后还须自然稳定性证据，#539不能仅凭CI或一次重启关闭。
