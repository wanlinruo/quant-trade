# 打板看板盘中采集器 `limit-up-board`（#648 P2）与页面（P3）

设计：`docs/superpowers/specs/2026-11-11-limit-up-board-dashboard-design.md` §4.3、§5.1、§5.2、§6。
代码：`src/qsys/data/board/live.py`（状态机）、`store.py`（存储）、`collector_cli.py`（常驻循环）。

## 运行节奏（交易日，Asia/Shanghai）

| 时段 | 动作 |
|---|---|
| 08:30 起 | 拉同花顺概念快照（`ths_index` type=N + 逐概念 `ths_member`，间隔 0.35s）与当日 `stock_st` |
| 盘前任务写入当日 `price_limits_daily` 后 | 连同昨日连板高度（`limit_list_d` / ST 用 `daily_flags`）一次性落库，当日输入不再变 |
| 09:15–15:05 | 每 5 秒读一次只读代理 `state:quote_realtime_latest`，按 `published_at` 去重；先写临时全量，再推进状态机、保存事件和情绪聚合 |
| 15:10 起 | 压缩：全量重放的情绪聚合与盘中记录逐行相等、序列（入选票 + 昨日涨停票，含昨收）回读逐值相等，才删除临时全量；否则保留并在 `day_meta.compact_error` 记原因 |
| 17:30–23:30 | 当日 `limit_list_d` 入库后对账（每 10 分钟重试），差异写 `mismatches`，不改写盘中观测 |

非交易日（warehouse 交易日历不含当日）不做任何事。

## 口径要点

- 封板 = 现价等于直取涨停价、卖一量为 0、买一价等于涨停价且买一量 > 0。买卖两侧量都为 0 时
  无法区分"字段缺失"与"真实为 0"（#647），记"封板未知"，不计入封板、首封、开板、回封。
- 2026-11-12 收盘快照（全推带盘口后）实测：按此规则判出 37 只封板，与当日 `limit_list_d`
  的 U 类 37 只逐只一致，无"封板未知"。
- 昨日 `limit_list_d` 或 `daily_flags` 未入库时，全体高度与晋级记未知（`day_meta.heights_error`），
  不回退为首板。概念快照失败时不分题材（`concept_error`），不使用其他口径替代。
- 当日 `stock_st` 为空（未发布）时 `st_published=0`，ST 标记显示未知，不用昨日名单代替。

## 存储

状态目录 `/data/qsys/var-prod/limit-up-board/`（数据盘）：

- `board.sqlite3`：长期按日留存（`day_meta`、`limits`、`prev_heights`、`st_symbols`、`concepts`、
  `concept_members`、`snapshots`、`events`、`board`、`series`、`mismatches`）。
- `raw/YYYYMMDD.sqlite3`：当日临时全量（约 5600 只 × 每 15 秒），校验通过后删除。删除授权仅限
  本组件当日临时全量文件（维护者 2026-11-11）。
- `status.json`：最近一次循环时间、阶段、最近快照与错误，供页面与健康检查读取。

两库 `journal_mode=DELETE`，Web 以 `mode=ro` 只读打开。读 warehouse 时短暂持有
`warehouse-write.lock` 共享锁（非阻塞重试，最长 120 秒），不长期占锁。

## 首次生产安装前的节点准备（需维护者确认后执行）

1. 建系统账号 `qsys-limit-up-board`（`--system --user-group`、nologin、home `/nonexistent`）。
2. 私有配置 `/etc/qsys/components/limit-up-board/deploy.toml`：只含 `[tushare]` token 与
   Redis/streams 段，`root:qsys-limit-up-board 0640`；不写入 Git。
3. Redis 只读代理：从已合并 main 的精确 SHA 安装 `redis_readonly_gateway.py`（`install`），
   `policy.json` 增加该 UID → `keys=["state:quote_realtime_latest"]`、`date_prefixes=[]`，
   `blocked_uids` 加入该 UID；`check` 通过后重启 `qsys-redis-reader.service` 并 reload
   netguard。只在非交易时段做；前后核对 web / health-observer 读取正常。
4. 节点白名单 `/etc/qsys/component-releases.json` 按 #511/#523 流程从精确 main 更新。
5. 安装基础 unit `deploy/systemd/qsys-limit-up-board.service`，状态目录加
   `group:qsys-observers:r-x`（含 default ACL），供 Web 只读（P3）。
6. 走正常组件发布（首次安装 `uninstalled` 路径），三方核对后 `enable --now`。

## Web 页面（P3）

- 路由 `/market/limit-up-board`（行情子导航"打板看板"）；只读 API
  `/api/market/limit-up-board?day=&at=` 与分时 `/api/market/limit-up-board/series?day=&symbol=`。
  代码 `src/qsys/web/limit_up_board.py`（`LimitUpBoardReader`）、模板 `limit_up_board.html`。
- Web 参数 `--limit-up-board-state /data/qsys/var-prod/limit-up-board`（测试环境指向不存在的
  目录，页面显示"尚未产生数据"）。读取只用 `mode=ro` + `query_only`，每次请求新开并关闭连接。
- 回放到快照时刻 T 只用 T 及之前的记录：情绪栏取 T 的聚合；入选/首封/开板/回封取 T 及之前
  事件；归列由当前归列按 T 之后的改列事件倒推；卡片状态取 T 时报价按同一封板口径判定。
  当日有临时全量时读全量，压缩后读序列。
- 模式：当日未对账为"盘中"，当日对账成功为"盘后定版"（差异以"≠ 直取值"标出），过去交易日
  为"历史"。AI 晋级列只读影子榜单 `limit_up_scores`：盘中取昨日预测，盘后/历史取当日预测。
- 尚未实现：封单额（盘口量单位未核实，见 `ths-concept-probe.md`）、盘后 `fd_amount` 直取展示、
  上线前历史日的高度梯队（P1 `history_cli` 已能汇总，页面暂只提示无留存）、分时叠加同题材前排、
  未被盘中观测到但直取在列的涨停票（对账目前只核对已观测票）。
