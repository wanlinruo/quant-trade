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
