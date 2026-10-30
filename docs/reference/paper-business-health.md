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
