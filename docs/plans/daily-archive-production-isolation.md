# 每日归档生产隔离（#485 / #330）

接续 #475、#476。实际生产阶段和证据以 #485 最新记录为准。

使用 qsys-daily-archive 私有主组+qsys-observers，最小配置仅 schema_version/tushare.token。
Windows 已验收的专属 key、显式 known_hosts、固定 gateway 与 hash 绑定分钟/竞价下载器接入；
不复制个人全权 key、不更改 Windows 任务/密码或行情交易进程。

四张写表为 auction_ticks、minute_bars、bar_minute_1m、suspend_daily。
工作目录与记录根保留既有路径/全部历史文件，迁移前保存内容hash/owner/group/ACL清单，
对子目录/文件补专属账号访问及Ubuntu兼容ACL，保留observers只读，设置对应default ACL。
状态目录使用0770和仅含本组件的私有组，让GNU install -d保留Ubuntu写ACL的有效mask；
真实安装器命令及双写者原子替换/消费者只读已做隔离探针，不加入Workbench写组。
不移动/删除旧Parquet/manifest/续传证据；Warehouse根及其余表只读，Workbench只读，
共享写锁使用只读描述符排他flock。旧任务峰值1.7G，初始MemoryHigh2G/Max3G、CPU50%、
单线程、DuckDB512MiB/spill1024MiB/私有临时目录，隔离实跑验证后再发布。

保留8天回看、全市场/首板候选/持仓并集、竞价优先、停牌刷新、逐股失败/退出码2、
失败后的成功文件导入及canonical质量门禁。午夜后目标只能是最近已收盘日。
新配置在全功能隔离副本实跑，生产Warehouse只读；真实权限/原子替换及当前生产消费者
通过后走正常CI/test/PR/final CI，三方发布成功后才进行真实业务验收，自然15:40另记。
当本来没有缺口时如实记录，不把零缺口路径当Windows下载或部分失败现场验收。
