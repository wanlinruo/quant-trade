# 计划 10：Web 改版最终集成

**日期：** 2026-11-19  
**Issue：** #662  
**边界：** 先在长期需求分支 `feat/web-redesign` 完成模块迁移、唯一入口与旧路径重定向；未经维护者确认不合入 `test/main`，不部署或重启生产组件。

## 当前事实

- 19 个设计页面及其新契约、查询层和前端已经完成；新应用位于 `qsys.webapp`。
- 原计划 02 的制品能力已经落地：CI 构建前端，wheel/sdist 携带 `spa_dist`，Web 组件制品构建会核对 `build-info.json` 与逐文件 SHA。
- 测试部署目前仍从源码 checkout 启动 `qsys-web`；最终集成必须额外验证包内前端制品，而不能只验证开发目录。
- `qsys.web` 仍是生产入口和旧页面所在地，共约 5,700 行；直接删除会同时破坏 Web、backtest-worker、health-observer、limit-up-shadow 和 monthly-research。
- 运维节点/版本快照目前只有消费契约，没有生产采集器；最终页面允许明确显示“未配置”，不在本计划夹带采集器或生产探测。

## 不变量

1. 先迁业务模块及真实消费者，再切 Web 唯一入口，最后删除旧页面代码；每一步保持可测试、可回滚。
2. 搬家提交只改变 Python 导入路径，不改 SQLite schema、文件格式、计算口径、发布路径或执行行为。
3. 过渡兼容模块只做符号转发并标明删除点；最终提交必须删除整个 `qsys.web` 包，不能长期双轨。
4. 旧页面路径只做固定表驱动的 308 重定向；不保留旧模板渲染，不把未知路径吞成首页。
5. 写接口、文件下载、会话与安全边界沿用新 `qsys.webapp` 契约；缺失数据保持 `null/missing`，不补值。
6. 本计划不生成运维快照、不确认已知警告、不解除停机闸，也不触发生产操作。

## Task 1：业务模块搬家（兼容阶段）

- [x] `web.workbench` 搬到 `qsys.backtest.workbench`，更新 backtest worker、paper 与新查询层消费者。
- [x] `web.research_jobs` 搬到 `qsys.research.job_console`，更新 monthly submit 与新查询层消费者。
- [x] `web.limit_up_shadow`、`web.limit_up_research` 搬到 `qsys.research`，更新发布/观察入口和新查询层消费者。
- [x] `web.service_views` 中观察器使用的服务状态与 Windows 任务查询搬到 `qsys.ops.service_console`，更新 health-observer。
- [x] 每次搬家先保留薄兼容转发，运行对应组件专项测试与导入清单，确认无循环导入和序列化变化。

完成证据：五类权威实现均已移出 `qsys.web`，旧模块只做同一类对象的符号转发；组件专项、入口导入与部署分类测试通过。首次全量回归仅发现三个新增研究路径未分类，已按真实消费者精确加入 Web、数据任务和 Research Runner，不使用宽泛通配。

## Task 2：唯一新应用入口

- [ ] 在 `qsys.webapp` 提供完整应用工厂与 CLI；`qsys-web` 和 `scripts/run_monitor.py` 改指向新入口。
- [ ] 直接构造全部新查询依赖，不再经旧 `MonitorService`、旧视图服务或旧 `create_app` 过渡接线。
- [ ] 健康端点、监听默认值、N-1 私有配置兼容、查询资源限制和 systemd 参数保持不变。
- [ ] 用实际 console script 从外部 cwd、仅安装 wheel 的环境启动，验证 `/healthz`、`/app`、静态资源和代表性 API。

## Task 3：旧路径 308 与旧包删除

- [ ] 建立显式旧路径到 19 个新页面的映射；保留 query string，仅允许站内固定目标。
- [ ] 为旧 GET/HEAD 页面验证 308；旧写接口不重定向，返回明确的 404/410，避免方法和请求体误送。
- [ ] 删除旧 `app.py`、模板、旧视图服务、兼容转发和整个 `qsys.web` 包。
- [ ] 把仍需保留的配置、急停状态读取、图表纯函数、worker CLI 放入对应领域包，并更新全部测试导入。
- [ ] `rg` 与 wheel 内容校验确认不再存在 `qsys.web` 导入、模板或旧路由。

## Task 4：测试部署与制品闭环

- [ ] 测试部署在目标 SHA 上先构建前端，再以与组件制品相同的 wheel 校验器验证 `spa_dist`。
- [ ] 测试服务从已验证安装产物启动，或提供等价的包内资源探针；不以源码目录中的 `frontend/dist` 冒充制品证据。
- [ ] 保留现有回滚、健康检查和部署记录语义；失败记录不覆盖，重试使用新 run-id。
- [ ] 核对可信 `main` planner 对新增/删除路径的分类；若需要 bootstrap，单独做最小控制面提交并先合入 main。

## Task 5：完整验收与合入门槛

- [ ] 后端专项、全量 `uv run pytest`、入口导入清单、仓库政策与 secret scan 全部通过。
- [ ] 前端单测、契约生成检查、typecheck、build、390/1280/1600 Playwright 全部通过。
- [ ] 记录页面级人工验收：19 个页面、空态/错误态、旧路径重定向、下载与写接口门禁。
- [ ] 推送稳定需求分支并在 #662 留证据；向维护者报告提交范围与剩余现场限制。
- [ ] 维护者确认后才 squash 合入 `test`；取得稳定 SHA 的 CI 与测试部署证据后，再创建 `test -> main` PR。
- [ ] PR 合并和每个生产组件发布分别再次确认；生产顺序至少覆盖受导入路径影响的 Web、backtest-worker、health-observer、limit-up-shadow 与 research-runner。

## Review Focus

1. 业务模块搬家是否只有路径变化，是否遗漏定时器、脚本、组件制品和测试之外的真实入口。
2. 新应用是否真正不再构造旧视图服务，旧写接口是否避免 308 方法保留带来的误操作。
3. wheel 中的前端资源是否与 build-info 和目标 SHA 一致，测试部署是否验证了安装后的实际读取路径。
4. 旧包删除后，生产 unit 的既有参数、私有配置、健康 URL 与数据权限是否仍兼容。
5. 页面完成、测试部署成功与生产采集器/生产激活是否被清楚区分。
