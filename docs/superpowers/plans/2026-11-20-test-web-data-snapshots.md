# 测试 Web 数据快照实施计划

**日期：** 2026-11-20

**Issue：** #683（由 #662 的测试数据面审计承接）

**边界：** 只读生产事实 → 测试专用不可变快照；不连接或复制 live、委托、成交、对账及真实账户状态。

## 已确认事实

- 页面改版 `d833577` 已在测试环境完成制品、HTTP 与布局验收，但 warehouse 主要只到
  2026-09-18，ServiceRun、health、board、alerts、paper 为空，nodes/releases 无生产者。
- 测试 Web 必须保持 shadow 姿态，不能为了页面非空连接生产 Redis 或 live SQLite。
- 仓库已有带逐文件 hash、manifest 与原子 `current` 的 warehouse snapshot；Research Runner
  已消费同一测试 snapshot store，但原控制面只在首次缺失时创建，不能从生产事实刷新。
- ServiceRun 与 alerts 是原子发布的文本事实；health 与 board 是活动 SQLite，必须用 SQLite
  backup 取得一致副本，不能直接复制主库/WAL/SHM。
- board 的 `raw/*.sqlite3` 是盘中全市场传输缓存，体积无界；历史页面所需事实已压缩进
  `board.sqlite3`，首阶段明确排除 raw。

## 数据面契约

| 资产 | 生产只读来源 | 测试发布位置 | 保留/一致性 |
|---|---|---|---|
| warehouse | `/data/qsys/warehouse` | `warehouse-snapshot-store/current/warehouse` | 持共享写锁建硬链接快照；逐文件 hash |
| ServiceRun | `/home/ubuntu/qsys/var/service-runs` | `web-ops-snapshot-store/current/service-runs` | 仅合法 JSON，近 35 天 |
| health | `var/health-observer/service-health.sqlite3` | `current/service-health.sqlite3` | SQLite online backup + integrity_check |
| board | `/data/qsys/var-prod/limit-up-board` | `current/limit-up-board` | board SQLite backup；status JSON；排除 raw |
| alerts | `/home/ubuntu/qsys/var/alerts` | `current/alerts/production` | 近 35 天合法 JSONL |

每份 ops 快照记录 `captured_at`、`source_revision`、截止日、文件大小/hash、内容总 hash、
非空计数和明确排除项。构建在 `.building-*` 中完成，全部校验通过后才原子切换 `current`；
失败保留旧指针。快照文件去写权限，Web unit 只读挂载。

## 控制面

1. `refresh-test-web-data.yml` 仅手工触发，默认 dry-run；正式执行必须输入
   `REFRESH_TEST_WEB_DATA`，不自动重放未知送达的写操作。
2. workflow 精确核对远端 `test` SHA、同 SHA 成功 CI、节点当前 SHA、健康与隔离状态，以及
   `web-data-refresh-v1` capability。
3. 固定节点入口只接受硬编码 allowlist，不允许 workflow 传任意源/目标路径。
4. warehouse 建快照时持共享锁，避免和生产写入交错；只在收盘后、自然数据任务结束后执行。
5. 两类快照通过后更新测试 warehouse 指针、重启测试 Web，并验证 health/services/board/alerts
   API；失败恢复 warehouse、ops 和旧 warehouse-current 三个指针后再恢复测试 Web。
6. 运行记录绑定测试 SHA/tree、生产代码 SHA、两类 snapshot id 与检查结果；不记录地址、凭据、
   账户或配置原文。

## 本阶段明确不做

- paper：生产模拟账户库与 Web workbench 共库，测试 Web 还可写。纳入前须另行设计一致性复制、
  标识脱敏及“快照种子 → 测试可写副本”契约，不能直接只读挂生产库。
- nodes/releases：需要独立脱敏生产者；不能让 Web 请求现场执行 systemd、journal、SSH 或 GitHub。
- research jobs：是否启用隔离 Runner 由既有 staging/drill 流程独立决定。
- #675 指数分时/涨跌幅分布、#676 AI 校准/漂移：继续由各自 Issue 承接。

## 验收顺序

1. 本地快照正负例、部署契约、workflow 安全、全量测试。
2. 控制面 bootstrap 合入 main；从精确 main 安装节点入口。
3. 稳定 test SHA CI 与测试部署成功后，先 dry-run，再在收盘后执行一次正式刷新。
4. 核对节点 record、manifest/hash、旧指针回滚目标和四类 API 非空/缺失语义。
5. 重新执行 19 页矩阵；当前阶段只把上述四类和 warehouse 标为可验收，paper、nodes、releases
   继续如实标注缺生产者，不据此批准生产页面切换。
