# 计划 02：测试环境消费 Web 不可变制品

**日期：** 2026-11-20  
**Issue：** #662  
**边界：** 只改 CI、测试部署控制面与 Web 静态资源选择；不触发测试部署，不改变生产 Web unit 或生产发布流程。

## 已确认事实

- CI 的 `package` job 已构建 `qsys-web-<sha>`，其中 `release.json` 绑定目标 commit/tree、组件规格、源码扫描清单和 wheel SHA。
- Web wheel 已包含 `qsys/webapp/spa_dist`，组件 verifier 会逐文件核对 `build-info.json`。
- 当前测试部署仍从 checkout 的 editable 环境启动；节点没有 Node，也不应重新构建前端。
- 测试部署 workflow 由可信 `main` checkout 执行，因此新传输协议必须先作为最小控制面变更合入 `main`，再部署页面改版目标。

## 设计

1. `deploy-test.yml` 只在 planner 选择 `test-runtime` 后，从触发它的精确 CI run 下载名为 `qsys-web-<TARGET_SHA>` 的制品，打成有大小上限的 ZIP，并计算 SHA-256。
2. 受限 SSH 的 `deploy` 命令新增必需的 `--artifact-sha256`；归档只经 stdin 传输，不落日志，不接受浮动分支或其他 run 的制品。
3. 节点入口先校验归档路径、符号链接、展开大小和顶层结构，再用目标 SHA checkout 中的 `build_component_release.py verify --component web` 完整复验。
4. 通过验证后，把 release 与从 wheel 提取并再次核对的 `spa_dist` 安装到测试专用不可变目录 `<web_release_root>/<sha>`；`current` 仅作原子指针切换。
5. 新应用可通过 `QSYS_WEB_SPA_ROOT` 选择外部静态资源。测试 unit 指向 `current/spa_dist`；生产 unit 不设置该变量，继续读取已安装 wheel 内资源。
6. 失败时恢复部署前的 `current` 指针、Git SHA、依赖和 unit；部署记录写入 Web 制品 SHA-256 与当前 release 身份。`/healthz` 和 `/app/today` 都必须返回 200。

## 实施清单

- [x] 新增外部 `spa_dist` 配置及负例测试。
- [x] 扩展测试节点入口的归档接收、严格解包、目标 SHA verifier、不可变安装、指针切换和回滚。
- [x] 扩展受限 SSH 白名单、入口 capability、节点配置 schema 与部署记录。
- [x] 修改测试部署 workflow，绑定上游 run/目标 SHA 下载并传输精确 Web 制品。
- [x] 补齐归档污染、哈希不符、重复文件、软链接、错误 component/SHA、回滚和 workflow 契约测试。
- [x] 运行部署专项、Web 专项、全量后端与前端验证。
- [x] 把本提交作为控制面 bootstrap 候选单独审查；未经维护者确认不推 `test/main`，不安装节点入口。
