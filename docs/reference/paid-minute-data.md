# 百度网盘付费 1 分钟数据

更新日期：2026-09-12

## 数据源与范围

- 提供方：余哈哈数据仓库；传输渠道：百度网盘。
- 远端目录：`/金融数据/A股分钟行情`。
- 本批范围：2009—2025 年 ZIP 中的 `1min` CSV；2026 年明确排除。
- 5/15/30/60 分钟源文件不入库，也不由 1 分钟数据计算后写入。
- 数值只取源 CSV 直接字段，不插值、不前后填充、不反推缺失分钟。

远端 17 个年度 ZIP 合计 63.35 GiB。1min 共 60,460 个证券年度文件，解压后
约 364.07 GiB。完整中央目录盘点保存在 Linux：
`/data/qsys/baidupan/minute-zip-inventory-20260912.json`。

## 落库字段

生产数据集为 `/data/qsys/warehouse/paid_minute_bars`，按年度和分片存储：

本表只存余哈哈付费源的数据，分两种布局：

- 年度分片 `year=YYYY/shard=NNNN`：按下文年度流程发布，必须有
  `paid_minute_quality/year=YYYY/quality-summary.json`；
- 日期覆盖 `year=YYYY/date=YYYYMMDD`：`scripts/publish_paid_minute_overrides.py`
  从付费暂存区原样挑出、替换 QMT「有成交、价格或成交额为零」的标的日。入库前
  必须通过 241 根网格、数值有效与 Tushare 日线对账（量/额/价相对偏差 ≤ 5%）。

JoinQuant/Tushare/QMT 数据写入各自具名数据集，查询层按「付费 → Tushare → 聚宽 →
QMT」的优先级逐标的日选源。常驻规则：`B2.paid_minute_structure`（年度分片有摘要）、
`B2.paid_minute_override_structure`（日期覆盖结构）、
`A3.paid_minute_override_gross_vs_daily`（粗大偏差阻断）、
`A3.paid_minute_override_vs_daily`（小差异挂 D-20）。

2026 年原始 CSV（172 个交易日）已于 2026-09-22 核验无损后删除：暂存区
`minute-rebuild/warehouse/paid_minute_bars_staging`（逐日源 SHA256 在 manifest）与隔离区
合起来是无损原始等价物，永久保留；远端网盘仍可原样重下。

2026 年分钟文件已知缺陷：03-03..03-11 约 68% 证券成交量错误而成交额正常，
详见 `production-data-management-audit-20260921.md` 5.3。

```text
paid_minute_bars/year=YYYY/shard=NNNN/part.parquet
paid_minute_bars/year=YYYY/date=YYYYMMDD/part.parquet
```

| 字段 | 类型 | 单位与处理 |
|---|---|---|
| symbol | string | 源代码，仅校验格式 |
| dt | timestamp(us) | 源分钟时间，仅解析格式，不平移 |
| open/high/low/close | float64 | 元，源直接值 |
| volume | int64 | 股，源直接值，非整数拒绝入库 |
| amount | float64 | 元，源直接值 |
| factor | float64 | 源累计复权因子 |

2025 年实测表头顺序是 `交易日期,分钟时间,证券代码,...`；2026 年松散文件实测为
`证券代码,交易日期,分钟时间,...`。两种实测版本均登记，未知字段或未知表头顺序拒绝入库。
2025 年的“分钟时间”本身包含完整日期时间，程序会核验它与“交易日期”一致。

## 年度执行流程

1. 从百度清单确认远端路径、`fs_id` 和精确字节数；
2. 下载到 `.part`，断线后按 HTTP Range 续传，每次重试刷新 dlink；
3. 完成后核对字节数并计算 ZIP SHA256；
4. 解析 ZIP 中央目录，过滤 `__MACOSX`、`.DS_Store` 和其他周期；
5. 完整读取每个 1min 条目，由 `zipfile` 校验条目 CRC；
6. 严格验证表头、证券代码、年度、交易日期、时间、OHLC、非负值、整数成交量、
   正复权因子和重复主键；核心空值原样进入隔离区；
7. 每 32 个证券写一个原子 Parquet 分片，先进入 `paid_minute_bars_staging`；
8. 对分钟数据按证券日聚合，仅用于与付费日线核验；
9. 在重叠期与 QMT 分钟数据逐键比较；
10. 无日线记录且全天零成交、四价恒定的完整占位日原样进入隔离区；
11. 质量通过后原子发布年度目录，随后删除本地年度 ZIP。

分片 manifest 保存源成员名、CRC、行数、证券数、时间范围和 Parquet SHA256；年度
manifest 另存 ZIP SHA256、原始文件名字节、压缩/解压大小及全部分片清单。中断重跑时，
只有 manifest 与文件 SHA256 同时匹配的分片才会跳过。

## 质量规则

硬约束：

- `(symbol, dt)` 零重复；
- 核心字段零空值、零 NaN、零无穷值；
- `low <= open/close <= high` 且价格大于零；
- `volume >= 0` 且必须为源整数，`amount >= 0`，`factor > 0`；
- 交易日期等于分钟时间日期；常规时段位于 09:30—11:30 或 13:00—15:00；
- 北交所 15:01—15:30 的源记录作为盘后/大宗交易观察值保留并单独统计；
- 每个来源成员完整读到 EOF 并通过 CRC。

与付费日线的核验计算不写入事实表：首分钟 open、日内最大 high、最小 low、末分钟
close 容差 0.005 元；分钟 volume 合计与日线手数乘 100 的容差为 0.5 股；amount 先按
100 元阈值报告；日内 factor 必须唯一并与日线 factor 相差不超过 1e-10。非 241 分钟的
证券日单列报告，不自行生成缺少分钟。

## 查询与数据源去重

只有已发布目录能被 `query_paid_minute` 读取，暂存和隔离数据不会进入查询。完成 2025 年
交叉验证后，付费 2025 是唯一生产分钟源，QMT 2025 重叠分区按去重方案清理；QMT 2026
保留，因为本批付费数据不含 2026。

## 运维命令

```bash
# 单年度断点续传
python scripts/baidupan.py get-file \
  /金融数据/A股分钟行情/2025.zip \
  /data/qsys/baidupan/A股分钟行情/2025.zip

# 流式暂存
python scripts/ingest_paid_minute.py stage \
  --zip /data/qsys/baidupan/A股分钟行情/2025.zip \
  --warehouse /data/qsys/warehouse --year 2025 --shard-members 32

# 质量报告
python scripts/ingest_paid_minute.py validate \
  --warehouse /data/qsys/warehouse --year 2025

# 严格隔离无日线记录的零成交恒价占位日，再重新生成质量报告
python scripts/ingest_paid_minute.py quarantine-placeholders \
  --warehouse /data/qsys/warehouse --year 2025

# 仅在报告通过并完成审阅后发布
python scripts/ingest_paid_minute.py publish \
  --warehouse /data/qsys/warehouse --year 2025
```

所有历史任务以低 CPU/磁盘优先级和 5 GiB 内存上限运行，并避开每日归档窗口。
