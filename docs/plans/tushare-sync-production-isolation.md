# Tushare 同步独立迁移（#475 / #476 / #330）

代码与生产验收分开记录，现场结果以 #475 最新评论为准。

组件使用 qsys-tushare-sync 私有主组和 qsys-observers，无登录 shell，不加入 Workbench 写组。
配置仅 schema_version 与 Tushare token；Windows 凭据由 #476 的专属 forced-command key、
显式 known_hosts 与固定 hash 下载器承接，保留有限晚到新股分钟收敛。

Warehouse 根只读，只允许 manifest 枚举的原始9表、派生5表、canonical收盘11表、
minute_bars/bar_minute_1m 与 metric_checks 写入。对这些目录设置组件写 ACL、Ubuntu 写 ACL、
observers 只读 ACL 和相应 default ACL；已有文件保持原值，原子替换后的文件须让现生产
Web/日报可读、Ubuntu 后续可写。组件独占状态与运行记录；Workbench 仅用于读取实际成交。
共享 warehouse-write.lock 使用只读文件描述符的排他 flock，不授予锁文件内容写权限。
质检 --artifacts 显式指向既有 Web 样本白名单根，不另迁样本路径。

原共享任务峰值约3.4GiB；初始 MemoryHigh=3G、MemoryMax=4G，CPUQuota=50%、单线程，
DuckDB 512MiB、spill 1024MiB、私有临时目录。隔离实跑核实峰值和退出码后才发布。

验证顺序：专项/全量与真实 Linux ACL/原子替换/读锁探针 → 精确 SHA CI/test/PR/final CI →
只更新可信控制面的 tushare-sync spec → 已收盘日隔离输出实跑、原生产文件哈希保持 →
当前生产消费者回读与负向权限 → 受控 dry-run/正式 workflow、Deployment、实际 validate_record
一致成功 → 新 unit 手工业务验收 → 17:00/19:00 自然任务单独跟踪。
不复制全权 Windows key、不移除晚到补采、不改真实交易开关、不删除数据或回退副本。

#476 真实生产入口于2026-10-21从已合并且CI成功的80f7068安装：原授权文件字节/ACL保留，
两组件独立key各自完成600000.SH当日分钟241行、竞价187行下载和长度/hash回传校验。
这证明受限入口与实际取数，不代替 #475 全链路业务或自然运行验收。
