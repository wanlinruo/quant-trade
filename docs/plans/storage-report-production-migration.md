# storage-report 首次生产迁移审阅包（#414）

状态：准备与只读盘点；**未获生产执行授权**。精确候选 SHA、artifact/wheel/spec hash、
预生产 workflow run 与记录由 #414 的最终验收评论固定。审核时必须同时查看该评论；
任何候选变化重新取得证据，不使用浮动 main 作为执行目标。

## 已核实现场（2026-10-18，只读）

- `qsys-storage-report.service`：loaded/inactive/dead，User/Group=ubuntu，无 drop-in；
  经 `/home/ubuntu/qsys/.venv/bin/python scripts/storage_report.py` 运行。
- `qsys-storage-report.timer`：enabled/active/waiting，下一次盘点时显示当日 18:20 CST。
  此时间会变化，不能把快照当未来维护窗口。
- `qsys-storage-report` 专用用户不存在，`/opt/qsys/components/storage-report/current` 不存在。
- `/home/ubuntu/qsys/var/storage-history`：0755、uid=1000/gid=1001，6 个 JSON 文件。
- 固定生产入口 v12，`/etc/qsys/component-releases.json` 不存在，须先升级至 v26。

## 分两次明确批准

A：固定入口/账号/目录准备；B：单组件正式切换、真实作业及回切验收。
批准前重新只读核验 unit、timer、现场 SHA、关键服务身份与生产健康，冲突则更新审阅包。
不得执行真实故障注入；如需该验收另行批准，正常回切也须包含在本次授权范围。

### A. 控制面与权限准备（审批后执行）

1. 在 `/data/qsys/var-deployments/storage-report-<候选SHA>/` 保存 before/manifest，
   记录 service/timer enabled/active、专属 current/drop-in 有无、入口/白名单有无及 hash、
   历史文件相对路径/hash/uid/gid/mode、Live/Web/Paper/Shadow/worker 身份。
   不读取或记录密码、完整部署配置、私钥、token。
2. 暂停 timer，等待 service inactive。保留 timer 原 enabled/active，不 enable/start service，
   禁止并发平台/组件发布或人工运行。跨时间窗口用节点侧 systemd-run 调度，不在 Mac 等待。
3. 从 GitHub 拉取获批精确 SHA，在独立临时 worktree 取得固定入口文件；不 reset 生产 checkout，
   不整目录 rsync。备份固定入口、forced-command wrapper、现有配置和白名单的有/无状态。
   **不要直接运行 `install_production_deployment_entrypoint.sh`**：它会覆盖现场部署配置。
   先在节点内验证现有配置的键集合与 schema_version=1 满足新入口，保留每个现场值；
   不把示例配置覆盖生产。若不兼容则停止，单独提交差异方案。
4. 经已核验 worktree，按“白名单 → wrapper → entrypoint”的顺序，以 root:root
   0644/0755 安装以下三个文件；不改 authorized_keys、个人 key、密码登录或节点其他配置：

   ```text
   deploy/component-releases.json → /etc/qsys/component-releases.json (0644)
   deploy/linux/qsys-production-deploy-ssh.py → /usr/local/libexec/qsys-production-deploy-ssh (0755)
   deploy/linux/qsys-production-deploy-entrypoint.py → /usr/local/libexec/qsys-production-deploy-entrypoint (0755)
   ```

5. 创建无交互登录的 `qsys-storage-report` 专用用户/组，不把它加入 sudo/个人组。
   `namei -l` 核对 `/data/qsys`、scratch、history 及 parent 的实际类型与读/遍历权限。
   该组件只读盘点，不需 Redis、Tushare 配置、Windows key 或生产 Warehouse 写权限。
   如需扩展只读 ACL，先列出精确路径和拟加权限再批准，不递归 chmod/chown Warehouse。
6. 历史目录保持原路径、6 份历史 JSON 的内容/hash 和原文件 owner/mode。
   仅调整**目录本身**为专用 owner/group 0750，保留 ubuntu 与 Web/读取者的只读/遍历权限。
   如需 ACL，保存/恢复原 ACL，并针对新建日期子目录与报告预置最小 default ACL；
   现有年/月/日子目录也须逐项列出：只调整这些目录的最小 ACL，给予专用用户创建权限、
   读取者遍历权限；仅 chown 顶层目录不足以在 ubuntu 拥有的旧日期目录内写新报告。
   不递归改动旧 JSON 的 owner/mode，不删除、不覆盖、不复制回旧快照。
   安装器只创建顶层目录，不修复旧日期目录或读者 ACL。
7. 以受限 status 核对 v26/能力、生产 checkout/健康不变，再做组件 workflow 的 dry-run。
   dry-run 不等于真实作业；任何控制面准备失败都停止并恢复备份，不顺势正式切换。

### B. 单组件切换与验收（另行批准后）

- 使用 `Deploy production component`，`component=storage-report`，`target_sha=#414 固定候选`。
  先 dry_run=true；维护者明确批准后才可 dry_run=false。保持 timer 暂停和 service inactive。
- 网关校验 exact SHA/tree、成功 CI/测试部署、制品/白名单一致后 stage 和切换。
  该次切换不应自动启动 inactive oneshot；核对 effective ExecStart 指向
  `/opt/qsys/components/storage-report/current/venv/bin/qsys-storage-report`、User/Group、
  ReadWritePaths 仅 history，current/read-only release 及部署记录正确。
- 在获批窗口单独触发一次 service：核验真实盘点成功、新增一份历史、schema/分类与硬链接
  去重口径、旧 6 份 hash 不变、读者可读、关键 peer 身份/健康不变。实际生产大目录扫描和
  盘容量是现场验收内容，预生产小夹具不替代这一项。
- 首次 previous current=uninstalled，不能冒称已具备 previous-successful 版本回滚。
  获批回切时恢复旧 drop-in 的有/无、daemon-reload，核对 effective legacy 命令；
  保留新制品和所有新增历史。按授权复验 legacy，再决定是否重新切新制品。
- 验收成功后按原 enabled/active 恢复 timer；原本 inactive 的 service 保持 inactive。
  在记录目录保存 after、报告 hash、部署 run/record、回切结果与未决项。

## 回滚与停止条件

- 控制面准备失败：按备份恢复旧入口/wrapper/配置；原白名单不存在时恢复该“无”状态。
  只恢复本次安装对象，不删除制品、回滚备份或历史。
- 组件切换失败：先核对 gateway/installer 的回滚记录；不得用重跑 workflow 掩盖原因。
  首次无旧 current 时，恢复原 drop-in/命令，保留全部新报告。
- 目录 owner/mode/ACL 恢复到 before 清单，新增历史保留并给予维护者读取权限；不得删除
  新文件来伪造“完全回到之前”。timer 仅恢复原状态，失败时保持暂停并记录人工处置。
- 任一 hash、有效入口、历史、权限、peer 身份或业务健康不符即停止，不推进下一组件。

本轮只准备该审阅包与隔离验证，以上生产步骤均未执行。
