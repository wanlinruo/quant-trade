# 原生本地分钟读取联合发布（#550 / #478 / #540）

维护者10-30明确授权尽快恢复数据链路/模拟链路，允许非交易时间按正常门禁尝试生产发布。
本次仅分钟读取、必要的已安装归档停机维护通道及两个数据消费者，不启动交易或恢复账户。

## 已有证据

- `qmt-local-minute-reads.md`及三份JSON记录只读SDK探针、精确差异与实际候选2410行/物理schema。
- 主业务脚本SHA `d5fb94b3c2fa4c824c557fad31835896135b9f1d0891d56d2c8fe7418eecc074`。
  加控制面时该脚本、现金管线/CLI、uv.lock不再变化；复用有效Windows验证，不重复请求。
- 第一次业务候选test6789703 CI41020744095通过。发布自审发现现有v38不支持Live停用态
  维护daily-archive，故补v39最小守卫；新稳定SHA单独全量/CI/test，不借旧证据。

## 门禁与执行次序

1. 源和可信main路径分类、完整CI、测试部署/真实validate_record、PR逐文件代行自审、
   正常合并/对齐/最终SHA制品。只下载daily-archive/Tushare实际需要的两个制品，验digest/ZIP/manifest。
2. 从已合并精确main安装可信v39控制面，备份原Python入口/owner/group/mode/ACL并验证；
   节点component规格与目标无差异，不改配置或全局白名单来绕过门禁。
3. 先确认当前实际时间已盘后，两个生产者任务终态/PID0、记录/KILL/主库/peer身份，
   留存原timer active/enabled及节点侧恢复保护，再暂停两个timer。绝不停止正在自然采集的任务。
   如果15:41归档仍在运行，等其自然终态；未知送达先读节点record/进程，写操作不自动重放。
4. Windows先只读确认固定config路径、旧minute脚本/hash、auction/dividend及roots/python
   完全保持。新脚本从精确GitHub制品安装到独立不可变目录；留旧文件/配置及完整ACL，
   先安装具备精确N/N-1兼容的Gateway入口，再把minute path/hash设为新脚本，
   previous_minute仅为原脚本精确path/hash；auction/dividend/python/roots不变。
   不扩展任意脚本、命令、来源或自动重放。两种SHA在隔离真实Gateway实例测试，
   保留原生产强制入口的授权边界。安装维护与两消费者切换仍在同一空闲暂停窗口，
   旧消费者或安装器补偿后的旧版本仍只能执行原批准SHA，不因版本先后失配。
   不改QMT、SSH、RDP、Autologon、任务业务配置或SDK，不下载年度数据/备份活动队列。
5. 依次正式发布两个组件，各等workflow最终success、GitHub Deployment与节点实际
   validate_record三方一致后验收。若失败以控制面已有失败补偿为准，保留失败记录；
   联合版本未齐不得直接恢复不兼容hash的生产者，自身维护失败不能演变为交易恢复。
6. 两个current中的下载器hash、新Windows固定hash、真实Gateway probe和必要PUT/字节hash
   读回一致；通过只读/独立审计路径验证候选，原业务仓库/检查点/KILL保持，原timer恢复。
   此次导入器未改，仍只给缺失stock-day生成清单，同键keep-last语义不偷偷改成来源门禁。
   不能据单次API相等覆盖历史，任何额外历史数据处理另按精确输入/范围授权。
7. 如果Tushare发布早于17:00，先保留#558旧观察请求、明确supersede旧timer，再以相同
   只读逻辑、新精确source SHA和`tushare`选源安排17:25；证明现金管线/CLI/lock与d505相同，
   不借旧SHA或旧QMT自然证据。晚于17:00则先读取既有报告，不覆盖已发生结果。
8. 正式激活、SDK/协议验收、原始值质量、自然任务分别记录。新下载器未真实自然调用时
   不关闭#550/#478，不因组件安装或主同步无缺口跳过分钟就宣布自然全市场已验收。

## v39归档维护通道

仅已安装daily-archive且Live inactive/disabled、已有可信状态绑定、非空KILL/主库；
归档timer须inactive，oneshot须inactive或真实failed终态/PID0/非零exit/唯一Invocation。
前后保留终态和九peer身份，真实validate_record验证审计记录。无其他组件的新增豁免，
不自动启动归档或Live，不reset-failed。先安装可信控制面，再安排组件正式发布。
