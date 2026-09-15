# qsys 策略 API

一个策略是**一个 `.py` 文件**。放哪都行，用 `--strategy` 指过去就能跑：

```bash
uv run python scripts/run_backtest.py \
    --strategy strategies/ma_cross.py \
    --start 2025-09-10 --end 2026-09-08 --cash 100000
```

`strategies/ma_cross.py` 是一个完整可跑的示例（约 80 行，含批量取数的正确
写法）。`strategies/structural_v1_2.py` 是聚宽移植版的装载入口。

---

## 一、文件里要有什么

**要么**给一个 `Strategy` 子类：

```python
from qsys.strategy.base import Strategy

class MyStrategy(Strategy):
    def on_bar(self, ctx, bars):
        ...
```

**要么**给一个 `create(deps)` 工厂——策略需要数据接口来构造时用：

```python
def create(deps):
    return MyStrategy(deps.feed, deps.market, deps.query)
```

两者都有时以 `create` 为准。一个文件里有多个策略类要用
`--strategy-name` 指定。

`deps` 带 `feed` / `market` / `query` / `calendar` / `params`。
这些是**构造期**依赖；运行期一律走 `ctx`，PIT 由框架强制。

### 工作台参数表（可选）

网页工作台可从模块级字典生成类型化表单；它由 AST 静态读取，保存草稿时
不会执行策略代码：

```python
PARAMETERS = {
    "window": {"type": "int", "default": 20, "min": 2, "max": 250,
               "label": "均线窗口", "description": "移动平均周期"},
    "mode": {"type": "str", "default": "fast", "choices": ["fast", "slow"]},
}
```

类型支持 `int`、`float`、`bool`、`str`。声明参数表后，未知参数与越界值会在
入队前拒绝；不声明则保持 `--param K=V` 的自由键值兼容行为。

## 二、生命周期钩子

按一个交易日内的发生顺序：

| 钩子 | 时刻 | 当日可见的数据 | 下单何时成交 |
|---|---|---|---|
| `on_init(ctx)` | 回测开始前一次 | — | — |
| `on_session_start(ctx)` | 09:15 | 当日 bar **不可见** | 次日开盘 |
| `on_auction(ctx)` | 竞价结算后（默认 09:30） | **只有 open** | 当日开盘价 |
| `on_checkpoint(ctx, at)` | 你声明的时刻 | 该时刻及更早的分钟线 | **当场按该时刻价** |
| `on_bar(ctx, bars)` | 收盘 | 当日 bar 全部可见 | 次日开盘 |
| `on_fill(ctx, fill)` | 每笔成交后 | — | — |
| `on_session_end(ctx)` | 15:30 | — | — |

只覆盖你关心的那几个，其余是空实现。

**`on_auction` 是一个刻意的窄口子**：`open` 在 09:30 就确定了，而
high/low/close 不是。所以那一刻 `ctx.market.session_open()` 只给
`open`，别的一律不给。

## 三、声明调度

对应聚宽的 `run_daily(func, '09:26')`。**写在策略里，不是写在运行器里**：

```python
def on_init(self, ctx):
    ctx.schedule.auction("09:30")
    ctx.schedule.checkpoint("11:25", "14:25", "14:50")
```

接受 `"09:30"`、`"09:30:00"` 或 `datetime.time(9, 30)`。
不声明就只有 `on_session_start` / `on_bar` / `on_session_end` 的默认节奏。

`ctx.schedule` **只在 `on_init` 里可用**——在别处声明不会生效，
所以直接报错而不是静默无效。

## 四、`ctx` 上有什么

### `ctx.data` —— 日线

```python
frame = ctx.data.get(symbols, start, end)          # polars DataFrame
frame = ctx.data.get(symbols, start, end, Adjustment.FORWARD)
```

**一次传全部标的**，不要逐只调——那是几十万次查询，回测会跑一整天。

`as_of` 由框架钉死在 `ctx.now`，策略**拿不到** `WarehouseQuery`。
这是 PIT 契约：未来数据在 API 层就进不来，而不是靠自觉。

### `ctx.market` —— 派生数据

```python
ctx.market.flags(symbols, start, end)       # 涨跌停 / 触板 / 炸板 / 首板
ctx.market.regime(start, end)               # 每日市场生态因子
ctx.market.valuation(symbols, day)          # 市值 / 换手
ctx.market.session_open(symbols, day)       # 当日开盘价（仅 on_auction 起可用）
ctx.market.auction_volume(symbols, day)     # 竞价成交量
ctx.market.limit_up_price(symbols, day)     # 当日涨停价
ctx.market.intraday(symbols, day, at)       # 某时刻的日内报价
```

同样绑定 `ctx.now`。

### `ctx.portfolio` —— 只读账户

```python
ctx.portfolio.cash            # 可用现金
ctx.portfolio.total_value     # 总资产
ctx.portfolio.symbols         # 持仓标的
pos = ctx.portfolio.position(symbol)
pos.total_qty, pos.available_qty, pos.avg_cost
```

`available_qty` 是 **T+1 已解冻**的部分。当日买入的不在其中——这条由引擎
的冻结机制保证，不靠策略自觉。

### `ctx.order` —— 下单

```python
ctx.order.submit(symbol, Side.BUY, qty)                       # 市价
ctx.order.submit(symbol, Side.BUY, qty, OrderType.LIMIT, px)  # 限价
ctx.order.target_qty(symbol, qty)       # 目标股数
ctx.order.target_value(symbol, value)   # 目标市值
ctx.order.target_weight(symbol, 0.2)    # 目标权重
```

整手取整、涨跌停、现金、可卖数量的检查在撮合前由约束系统统一做，
策略不必重复判断。被削减或拒绝的单会进 `rejects.csv`，**不会静默丢弃**。

### 其余

```python
ctx.now        # 当前时刻（datetime）
ctx.state      # 跨钩子的可变状态，相当于聚宽的 g
ctx.params     # 运行器传进来的参数（--param k=v）
ctx.log        # structlog
```

## 五、落盘

每次运行落一个自包含目录 `var/runs/<名字>/`，含 zip。**文件名与列名
对齐聚宽的研究导出**，两边可以直接 diff：

```
daily_nav.csv  daily_buy_details.csv  closed_lots.csv  gate_decisions.csv
regime_daily.csv  branch_summary.csv  run_status.csv
fills.csv  rejects.csv  metrics.csv  config.json      <- 这几个聚宽没有
```

浏览器里在 `/backtests` 可以列出、看净值曲线、逐个下载。
两个运行目录之间的对账用 `scripts/reconcile.py`。

## 六、网页回测工作台

监控台启动时会同时启用个人策略工作台：

```bash
uv run python scripts/run_monitor.py \
    --warehouse warehouse --runs var/runs --workspace var/workbench
```

打开 `/strategies` 可新建策略或把 `strategies/*.py` 导入为托管副本。保存只更新
草稿；点击“保存并运行”才会以源码哈希固化不可变版本并进入单任务后台队列。
任务页展示交易日进度、日志、失败原因与取消按钮，成功后自动切换为研究报告。

工作台默认仍只监听 `127.0.0.1`，定位是个人私网中的可信代码执行环境。策略
在独立子进程运行并有超时和取消保护，但它不是面向不可信用户的安全沙箱。

## 七、几条容易踩的

- **逐只取数**：`ctx.data.get([一只])` 放在循环里，回测慢几百倍。批量取。
- **在 `on_bar` 里用当日 `open` 下单**：可以，但那单是**次日**开盘成交。
  要当日开盘成交就用 `on_auction`。
- **忘了 `available_qty`**：`total_qty` 里含当日买入的，卖不掉。
- **自己算涨停价**：用 `ctx.market.limit_up_price()`。自己按 10% 算会在
  创业板（20%）和 ST（5%）上错。
