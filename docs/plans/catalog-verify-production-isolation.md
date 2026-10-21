# catalog-verify 生产隔离（#469 / #330）

2026-10-21 开始实施。当前代码和生产进度以
[#469](https://github.com/wanlinruo/quant-trade/issues/469) 最新记录为准。

## 路径与身份

- 保留现有 release/current 与冻结 baseline；以 qsys-catalog-verify 无登录账号运行，主组
  私有同名组，仅补充只读 qsys-observers。#479实测qsys-workbench同时能写工作台，
  因此不用于只读组件；home已对observers开放遍历，权限以新身份实测为准。
- `/etc/qsys/components/catalog-verify/deploy.toml` 为 root:私有组0640，仅 schema_version、
  Tushare token；不输出凭据，原完整配置 hash 保持不变。
- `--service-runs` 仅写专属 `service-runs/catalog-verify`；新增 `--service-runs-input`
  从全局记录只读检测 tushare-sync，保持“同步仍运行则跳过”的保护。默认值兼容原入口。
- 显式 `--artifacts /home/ubuntu/qsys/artifacts/metric_checks`，不再从专属
  记录目录的祖先推断样本位置。保留现生产 Web 的样本下载白名单；原共享记录和旧样本保留，
  不迁移样本根、不覆盖历史样本。
- baseline 继续 ubuntu:ubuntu0640，账号只读 ACL；目录只加遍历/读取，保留原 ACL。
  ExecStartPre 拒绝缺失/空文件；安装器拒绝升级时用旧 seed 重建丢失的冻结 baseline。
- ProtectSystem=strict 与 ProtectHome=read-only 继承基础 unit；重置宽泛 ReadWritePaths，
  仅开放 metric_checks、dataset_checks、relation_checks、原规则样本根与自身记录五处。
  原 warehouse-write.lock 保持排他；不为锁开放整个 var 的写权限。
- 三类质检表与原样本目录按真实原子写语义设置组件 rwX access/default ACL；新分区必须保留 ubuntu
  的 rwX 与 observers 的 r-X ACL。业务表不授予写权限，不能因验证权限而改业务事实。
- 保留每次 DuckDB 512MiB、spill 1024MiB、单线程预算；unit MemoryMax=3G、
  MemoryHigh=2G、CPUQuota=50%、Nice=10、IO idle。

## 发布前证据

1. 真实 Linux 隔离目录依次执行 install/chown/0640、文件 ACL、原子替换、后续写者回写；
   专属账号可写三类模拟结果目录，ubuntu 可合并重写，当前 Web/日报可读；业务目录写入拒绝。
   用当前实际消费者的用户/venv验证新旧记录发现及去重；不凭 root 或 mock 成功放行。
2. 稳定候选制品以真实上一已收盘交易日输入、隔离输出执行。业务 warehouse 只读；三张结果表
   采用独立副本，禁止用硬链接让候选改到生产分区。保留 baseline hash；不下载或物化整年分钟表。
3. 专项测试、全量 pytest、repo policy、可信 main 与候选 planner 路径分类；再集中推送，取得
   test CI → 测试部署 → test/main 发布 PR、自审与 PR CI，合并后复用自动 final SHA CI。
4. 只对已合并 main 的 catalog 规格更新可信白名单；准备账号、最小配置、精确 ACL 和读者探针。
   维护窗口内暂停 timer、确认 inactive，正式 workflow dry-run 成功后再正式发布。
5. workflow、GitHub Deployment、节点实际 validate_record 三方成功后才做业务验收；核对
   current/身份/沙箱/读者/历史/配置与 baseline hash/Live Core 等 peer 不变，再恢复 timer。
   19:40 自然作业单列验收；本次若错过该窗口，不能把手工实跑写成自然调度成功。

不新增生产 legacy 回切或故障注入；异常向前修复。控制面失败补偿保留，生产数据与回退副本不删除。

## 后续组件顺序

本项完成后优先 tushare-sync → daily-archive → premarket-limits，逐项落实专属配置、
输入/输出和当前消费者兼容；随后 paper/shadow-core 与 reboot-drill。Live Core 最后。
研究 Runner、平台、Windows 属于独立工作域，不用14组件计数代替全部工作完成。

## 已取得证据（2026-10-21）

- PR#472 已合并，候选 main/test `3e40c218d266ed00706952fef4c66711412119ef`；
  源CI39593880066、测试部署39594088976/Deployment7225135353、PR CI39594255503、
  最终CI39594463980均成功。可信main/候选planner无未知路径，55项专项与本地全量pytest通过。
- 真实账号/最终制品/最小配置/受限systemd实跑10-21已收盘日：636条、0阻断、28警告；
  CPU52.955s、峰值1.7G、swap0；三类结果表全部旧行保留，生产结果与baseline hash未变。
  当前Web验证记录和样本下载；当前日报验证记录和质检结果读取。
- 探针按实际消费者职责调用，不假设旧日报支持Web的新API。审计父目录也必须具备读者遍历ACL。
- 首次正式workflow39596077181/Deployment7225444858在切换前被可写缓存门禁拒绝：
  维护账号Python在已chmod的候选venv新建775缓存目录。current保持dfd65bf，自动补偿记录为
  rolled_back，未写生产数据；#473记录权限修复和`-B`规则。保留失败record，重发使用新run-id，
  不重跑未变CI/业务计算、不放宽安装门禁。
- 证据根 `/data/qsys/var-deployments/issue469-20261021/`；正式发布及后续自然作业状态以
  #469最新记录为准，本节不提前宣告完成。

正式重发39596600730、GitHub Deployment7225528413、节点实际validate_record三方success；
current为3e40c21，专属账号和五处写集合生效，严格preflight16/16，所有关键peer身份不变。
timer已恢复，下次10-22 19:40；20:05启动发布后生产验收作业，后续结果见#469。
10-22 19:50自然业务采证已由节点systemd timer调度，不用手工实跑代替自然验收。
