# 停机态历史状态读取（#544）

关联 [#544](https://github.com/wanlinruo/quant-trade/issues/544)、
[#533](https://github.com/wanlinruo/quant-trade/issues/533)。本方案不恢复实盘。

## 问题与边界

旧 Live 按授权停止后，最后一个 SQLite 连接关闭会移除 WAL/SHM。只有目录读取权限的
checks/observer 在原只读沙箱中无法重建辅助文件，报 CANTOPEN。#529 的生产者连接锚点
覆盖运行期，不能覆盖生产者停机期。目前临时服务约 10-27 23:51:46 到期。

新增独立组件 `live-state-reader`，仅用标准库以 mode=ro、query_only 打开已有 WAL 库，
保持不含事务的连接，不创建数据库、修改 schema/业务事实或切 journal mode。不导入
LiveStore、Runtime、Redis、账户、通知或策略模块。日志只说明历史库可读，不报告交易健康。
未知/损坏/缺失文件、非 WAL、缺历史表或文件替换均失败退出。

服务使用实际生产者身份 qsys-live-core，沿用已验证的辅助文件权限继承。只开放状态目录
以创建 SQLite sidecar，主库、KILL 和实际锁文件在 unit 内只读；完整配置不可见、网络隔离。
消费者 UID、组、目录权限不放宽，不复制账本或用 immutable 绕过 WAL。

文件只读挂载在某些组合下会保留原 inode，因此另以目录级只读视图验证主库身份；使用
`BindReadOnlyPaths=…:…:norbind`，不递归复制目录下的文件挂载。配置语义见
[systemd.exec 官方说明](https://freedesktop.org/software/systemd/man/latest/systemd.exec.html)。
连接每次短 SELECT 都结束，不持有旧快照；普通写者的新提交与 checkpoint 不被阻挡。
制品扫描范围包含入口模块及两个包初始化文件，均由 manifest 逐文件指纹绑定。

## 独立安装门禁

现有组件发布要求活动 Live 身份与实盘 preflight；不能为发布读库修复启动旧交易核心。
固定入口 v33 仅对 `live-state-reader` 使用独立停机守卫：

- Live 必须 loaded、inactive/dead、MainPID0、UnitFileState=disabled。
- 原稳定绑定须为 root 所有、不可组/其他写、内容准确的普通文件。
- 既有主库及非空 KILL 必须存在且为普通文件，前后 SHA/设备/inode/mtime/权限/owner/group一致。
- 发布保持所有必要 peer 身份、生产共享 checkout 及目标组件原 active 状态不变。
- source/CI/测试部署/tree、制品 digest/spec、实际 record validator 等门禁不变。

其他组件没有停机例外，Live 发布自身仍要求原服务 active，不能借该通道激活交易。
读库组件首次发布保持 inactive；安装成功与正式服务激活分别记录。

## 隔离证据（10-27 11:12）

源 SHA256 `7e6005fad8effe46d75df9b471edd9ac3aae59b53596c1a94d36fcddeaeb100d`。
`scripts/verify_live_state_reader_preproduction.py` 在新建合成库、实际 qsys-live-core 身份与
匹配沙箱下执行；没有打开生产业务库，也未修改/重启生产服务。

| 场景 | 结果 |
|---|---|
| 冷库，真实 checks/observer UID 与只读沙箱 | 两者 CANTOPEN，基线复现 |
| 辅助连接存在 | 两者均读到 1 条合成记录 |
| 真实写者插入、提交、checkpoint、关闭与 GC 后 | 两者均读到 2 条合成记录 |
| 辅助连接 SQL 删除，主库/只读视图/KILL/锁的文件写入 | 五项全部拒绝 |
| 宿主替换合成主库 | 检测替换并 exit1，保留终态及 journal |
| 生产主库与 KILL 的独立原始指纹核对 | SHA/mtime/mode/UID/GID 不变 |

最终记录 `/data/qsys/var-deployments/issue544-sandbox-111207/result.json`，
独立生产指纹比较在 `issue544-candidate-111207/execution.json`。
临时合成 unit 已停止并清除其 failed 标记，终态/journal保留在审计里；原五项生产/历史
failed units 保持。未执行生产故障注入。

首次 110851 记录的最后一个断言错误地只接受“目录视图检测替换”的文案，实际已由主路径
先检测到替换并 exit1。修正 verifier 精确接受两个合法守卫错误后，以新根 111021 复验通过；
最终 111207 又覆盖 KILL/锁与完整限制。应用源码未变，失败记录未覆盖或冒充成功。

本地最终全量 4022 passed、7 skipped、2条既有 deprecation warnings；271项专项通过。
外部cwd、仅src路径的真实脚本调用通过，wheel入口/源码一致、secret scan与路径分类通过。
首轮全量的CI安全测试步骤计数因新增两步由38变40，修正并验证每步的非PR限制后重跑全量；
没有改变运行门禁。精确SHA的远端CI/测试部署及生产证据仍须分别记录在Issue。

## 审批后的执行顺序

本节是待执行方案，不是生产完成记录。生产发布、辅助服务激活均须维护者明确确认。

1. 稳定 test SHA 的 CI、测试部署成功，PR 自审与 PR CI 成功后按正常流程合并。
   本轮修改 ci.yml，source/controller workflow 哈希会变化：旧可信 main 下不强行复用；
   合并后先查 Align，确需 bootstrap 时仅按 AGENTS §2.1 的完整门禁精确对齐 test，
   取得新控制器下的证据。不能用空提交、降低 evidence 校验或重放无变化 CI 消除门禁。
2. 从已合并的精确 main、干净且 owner 正确的工作树，审查/备份旧控制面后用
   `scripts/install_production_deployment_entrypoint.sh` 安装 v33 与 manifest。
   比较节点/目标组件规范 JSON 指纹。旧业务代码、Live/Paper/Web进程不随此步升级。
3. 新 service 的基础 unit 属必要准备件：从同一精确 main 安装
   `deploy/systemd/qsys-live-state-reader.service` 至同名 `/etc/systemd/system/` 路径，
   原件如存在先核对并备份；daemon-reload，不 start/enable。它不是共享业务部署，
   不为一个新基础 unit 重启其他组件。候选 executable 尚未安装时不把 verify 的缺文件报错
   当运行验收；制品安装后再完整 systemd-analyze verify。
4. 对该精确 SHA 和组件先执行 workflow dry_run=true；确认停机、KILL、状态与 peer 守卫
   通过后，按本次明确审批执行 dry_run=false，使用新 run-id。等待 workflow 终态成功、
   GitHub Deployment success、节点实际 validate_record 三方一致；此时组件仍 inactive。
5. 核对合成证据对应源码/最终制品指纹、root 所有和 go-w、实际 unit 沙箱及启动入口。
   按明确审批仅 enable/start `qsys-live-state-reader.service`。临时锚点先保持，避免 sidecar
   生命周期空档；真实 checks/observer UID 与相同只读沙箱验收历史读取，前后主库/KILL
   指纹不变、Live仍PID0。新服务 active、可读和全部事实核验完成后，仅停止原临时锚点。
6. 永久服务有故障就记录、修复并前进；不复制旧库覆盖新事实，不解除 KILL。安装失败沿用
   既有自动代码补偿。若窗口不足，及时交接临时服务的精确期限，不盲目重放旧 activation。

读库恢复不代表停机期间策略执行。原失败 journal/service-run 保留，缺分钟/竞价继续由
#540 告警；计划停机与未执行事件的产品展示由 #482 分层处理，不把缺记录伪造为成功。
